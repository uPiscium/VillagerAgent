import json
import subprocess
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from env.minecraft_bridge_diagnostics import (
    BoundedDiagnosticRecorder,
    CORRELATION_HEADER,
    artifact_projection,
    classify_request_exception,
    install_fastapi_request_diagnostics,
    read_diagnostic_snapshot,
)
from env.minecraft_client import (
    Agent,
    MinecraftBridgeCleanupError,
    MinecraftToolTimeoutError,
    _minecraft_request,
)
from env.runtime_paths import RuntimePaths, atomic_write_json
from start_with_config import _runtime_checkpoint_result, _runtime_result


def _flush_agent(actor="Alice"):
    recorder = Agent._caller_diagnostic_recorder(actor)
    assert recorder is not None and recorder.flush()


@pytest.fixture
def diagnostic_agent(tmp_path, monkeypatch):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    paths.ensure_directories()
    atomic_write_json(paths.url_prefix, {
        "Alice": "http://localhost:5000",
        "Bob": "http://localhost:5001",
    })
    monkeypatch.setattr(Agent, "runtime_paths_by_name", {"Alice": paths, "Bob": paths})
    monkeypatch.setattr(Agent, "name2port", {"Alice": 5000, "Bob": 5001})
    monkeypatch.setattr(Agent, "_bridge_diagnostic_recorders", {})
    monkeypatch.setattr(Agent, "last_tool_timeout", None)
    monkeypatch.setattr(Agent, "last_bridge_diagnostics", None)
    yield paths
    Agent._close_bridge_diagnostic_recorders()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.ConnectTimeout("blocked"), "connect_timeout"),
        (requests.ReadTimeout("blocked"), "read_timeout"),
        (requests.ConnectionError(ConnectionRefusedError(111, "refused")), "connection_refused"),
        (requests.ConnectionError("down"), "connection_error"),
        (requests.RequestException("bad"), "other_request_error"),
    ],
)
def test_request_failure_types_are_classified(error, expected):
    assert classify_request_exception(error) == expected


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [(requests.ConnectTimeout, "connect_timeout"), (requests.ReadTimeout, "read_timeout")],
)
def test_timeout_records_classification_and_monotonic_elapsed_time(
    diagnostic_agent, monkeypatch, error_type, expected,
):
    ticks = iter((100, 350))
    monkeypatch.setattr("env.minecraft_client._request_monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(
        "env.minecraft_client.requests.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error_type("blocked")),
    )

    with pytest.raises(MinecraftToolTimeoutError) as raised:
        _minecraft_request("POST", "http://localhost:5000/post_move_to_pos")

    assert raised.value.failure_detail["timeout_type"] == expected
    assert raised.value.failure_detail["outcome_certainty"] == "unknown"
    assert raised.value.failure_detail["retry_safe"] is False
    _flush_agent()
    snapshot, error = read_diagnostic_snapshot(
        diagnostic_agent.minecraft_bridge_caller_diagnostics
    )
    assert error is None
    terminal = snapshot["events"][-1]
    assert terminal["event_type"] == "caller_request_timed_out"
    assert terminal["timeout_type"] == expected
    assert terminal["started_monotonic_ns"] == 100
    assert terminal["completed_monotonic_ns"] == 350
    assert terminal["elapsed_ns"] == 250
    assert terminal["configured_connect_timeout_s"] == 5.0
    assert terminal["configured_read_timeout_s"] == 30.0


def test_ping_lifecycle_is_actor_scoped_and_timestamped(diagnostic_agent, monkeypatch):
    ticks = iter((1000, 1600))
    monkeypatch.setattr("env.minecraft_client._request_monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(
        "env.minecraft_client.requests.request",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, json=lambda: {
            "message": "pong", "status": True,
        }),
    )

    assert Agent.ping("Alice")["status"] is True
    _flush_agent()

    snapshot, _ = read_diagnostic_snapshot(diagnostic_agent.minecraft_bridge_caller_diagnostics)
    ping = [event for event in snapshot["events"] if event["event_type"].startswith("ping_")]
    assert [event["event_type"] for event in ping] == ["ping_started", "ping_succeeded"]
    assert all(event["actor"] == "Alice" for event in ping)
    assert ping[0]["correlation_id"] == ping[1]["correlation_id"]
    assert ping[1]["elapsed_ns"] == 600


def test_ping_connection_failure_keeps_single_correlated_terminal_event(
    diagnostic_agent, monkeypatch,
):
    monkeypatch.setattr(
        "env.minecraft_client.requests.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ConnectionError("down")),
    )

    assert Agent.ping("Alice")["status"] is False
    _flush_agent()

    snapshot, _ = read_diagnostic_snapshot(diagnostic_agent.minecraft_bridge_caller_diagnostics)
    failures = [event for event in snapshot["events"] if event["event_type"] == "ping_failed"]
    assert len(failures) == 1
    assert failures[0]["correlation_id"]
    assert failures[0]["timeout_type"] == "connection_error"


def test_ping_timeout_preserves_return_contract_and_single_terminal_event(
    diagnostic_agent, monkeypatch,
):
    monkeypatch.setattr(
        "env.minecraft_client.requests.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ReadTimeout("stalled")),
    )

    assert Agent.ping("Alice") == {'message': 'Exception', 'status': False}
    _flush_agent()

    snapshot, _ = read_diagnostic_snapshot(diagnostic_agent.minecraft_bridge_caller_diagnostics)
    terminal = [event for event in snapshot["events"] if event["event_type"] == "ping_timed_out"]
    assert len(terminal) == 1
    assert terminal[0]["correlation_id"]
    assert terminal[0]["timeout_type"] == "read_timeout"


def test_ping_invalid_error_response_preserves_transport_correlation(
    diagnostic_agent, monkeypatch,
):
    response = requests.Response()
    response.status_code = 500
    response._content = b"not-json"
    monkeypatch.setattr("env.minecraft_client.requests.request", lambda *_args, **_kwargs: response)

    assert Agent.ping("Alice")["status"] is False
    _flush_agent()

    snapshot, _ = read_diagnostic_snapshot(diagnostic_agent.minecraft_bridge_caller_diagnostics)
    failure = [event for event in snapshot["events"] if event["event_type"] == "ping_failed"][-1]
    assert failure["correlation_id"]
    assert failure["status_code"] == 500


def test_request_correlation_pairs_caller_and_bridge_events(
    diagnostic_agent, monkeypatch, tmp_path,
):
    bridge_recorder = BoundedDiagnosticRecorder(
        tmp_path / "bridge.json", producer="bridge", actor="Alice",
    )
    app = FastAPI()
    install_fastapi_request_diagnostics(app, bridge_recorder, actor="Alice")

    @app.get("/post_ping")
    async def ping():
        return {"status": True}

    client = TestClient(app)

    def request(method, unused_url, **kwargs):
        return client.request(method, "/post_ping", headers=kwargs["headers"])

    monkeypatch.setattr("env.minecraft_client.requests.request", request)
    response = _minecraft_request("GET", "http://localhost:5000/post_ping")
    assert response.status_code == 200
    _flush_agent()
    assert bridge_recorder.flush()

    caller, _ = read_diagnostic_snapshot(diagnostic_agent.minecraft_bridge_caller_diagnostics)
    bridge, _ = read_diagnostic_snapshot(tmp_path / "bridge.json")
    caller_id = caller["events"][0]["correlation_id"]
    received = next(event for event in bridge["events"] if event["event_type"] == "request_received")
    completed = next(event for event in bridge["events"] if event["event_type"] == "request_completed")
    assert received["correlation_id"] == caller_id == completed["correlation_id"]
    assert received["caller_correlated"] is True


def test_stalled_received_request_is_distinguishable_from_unreachable_endpoint(
    diagnostic_agent, monkeypatch, tmp_path,
):
    bridge = BoundedDiagnosticRecorder(tmp_path / "bridge.json", producer="bridge", actor="Alice")
    fixed_id = "a" * 32
    monkeypatch.setattr("env.minecraft_client.new_correlation_id", lambda: fixed_id)

    def received_then_stalled(_method, _url, **kwargs):
        bridge.record(
            "request_received", correlation_id=kwargs["headers"][CORRELATION_HEADER],
            actor="Alice", route="/post_find", method="POST",
            endpoint_identity="actor:Alice", started_monotonic_ns=1,
            caller_correlated=True,
        )
        raise requests.ReadTimeout("stalled")

    monkeypatch.setattr("env.minecraft_client.requests.request", received_then_stalled)
    with pytest.raises(MinecraftToolTimeoutError):
        _minecraft_request("POST", "http://localhost:5000/post_find")
    assert bridge.flush()
    received_snapshot, _ = read_diagnostic_snapshot(tmp_path / "bridge.json")
    assert [event["event_type"] for event in received_snapshot["events"]] == ["request_received"]

    bridge_unreachable = BoundedDiagnosticRecorder(
        tmp_path / "unreachable.json", producer="bridge", actor="Bob",
    )
    monkeypatch.setattr(
        "env.minecraft_client.requests.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ConnectTimeout("down")),
    )
    with pytest.raises(MinecraftToolTimeoutError):
        _minecraft_request("GET", "http://localhost:5001/post_ping")
    assert not (tmp_path / "unreachable.json").exists()
    assert bridge_unreachable.snapshot()["events"] == []


def test_bridge_ready_and_not_ready_lifecycle_is_persisted(tmp_path):
    ready = BoundedDiagnosticRecorder(tmp_path / "ready.json", producer="bridge", actor="Alice")
    ready.record("listener_startup_completed", actor="Alice", expected_local_port=5000)
    ready.record("listener_ready", actor="Alice", expected_local_port=5000)
    not_ready = BoundedDiagnosticRecorder(tmp_path / "not-ready.json", producer="bridge", actor="Bob")
    not_ready.record("listener_starting", actor="Bob", expected_local_port=5001)
    not_ready.record("listener_failed", actor="Bob", expected_local_port=5001,
                     error_class="OSError")
    assert ready.flush() and not_ready.flush()

    ready_snapshot, _ = read_diagnostic_snapshot(tmp_path / "ready.json")
    failed_snapshot, _ = read_diagnostic_snapshot(tmp_path / "not-ready.json")
    assert ready_snapshot["events"][-1]["event_type"] == "listener_ready"
    assert failed_snapshot["events"][-1]["event_type"] == "listener_failed"


def test_failure_artifacts_survive_bridge_shutdown(diagnostic_agent, monkeypatch):
    class Process:
        pid = 12345
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr(Agent, "agent_process", {"Alice": Process()})
    Agent.record_bridge_diagnostic(
        "Alice", "caller_request_failed", actor="Alice", correlation_id="b" * 32,
        route="/post_find", error_class="ConnectionError",
    )

    cleanup = Agent.kill()

    assert cleanup["cleanup_complete"] is True
    summary = Agent.last_bridge_diagnostics
    assert summary["artifacts"]["caller"]["state"] == "valid"
    assert summary["actors"]["Alice"]["process_lifecycle"][-1]["event_type"] == "bridge_process_exited"


def test_process_still_alive_is_not_recorded_as_exited(diagnostic_agent, monkeypatch):
    class Process:
        pid = 12345

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("bridge", timeout)

    monkeypatch.setattr(Agent, "agent_process", {"Alice": Process()})

    with pytest.raises(MinecraftBridgeCleanupError):
        Agent.kill(terminate_grace_seconds=0, kill_grace_seconds=0)

    lifecycle = Agent.last_bridge_diagnostics["actors"]["Alice"]["process_lifecycle"]
    assert lifecycle[-1]["event_type"] == "bridge_process_still_alive"


def test_untrusted_artifact_fields_are_removed_from_projection(tmp_path):
    path = tmp_path / "bridge.json"
    secret = "never-retain-this-secret"
    path.write_text(json.dumps({
        "schema_version": "minecraft-bridge-diagnostics/1",
        "producer": "bridge",
        "actor": "Alice",
        "events": [{
            "event_type": "request_received",
            "route": "/post_find",
            "payload": secret,
            "request_body": secret,
        }],
    }), encoding="utf-8")

    projection = artifact_projection(path, runtime_root=tmp_path)

    assert projection["state"] == "valid"
    assert projection["snapshot"]["events"] == [{
        "event_type": "request_received",
        "route": "/post_find",
    }]
    assert secret not in json.dumps(projection)


def test_recorder_close_stops_writer_and_rejects_new_events(tmp_path):
    recorder = BoundedDiagnosticRecorder(tmp_path / "bridge.json", producer="bridge")
    assert recorder.record("listener_starting")

    assert recorder.close()
    assert not recorder._writer.is_alive()
    assert recorder.record("listener_ready") is False


def test_diagnostics_are_bounded_and_do_not_record_payloads_or_secrets(tmp_path):
    path = tmp_path / "diagnostics.json"
    recorder = BoundedDiagnosticRecorder(path, producer="bridge", actor="Alice", max_events=2)
    for index in range(3):
        recorder.record(
            "request_received", actor="Alice", route="/post_find",
            correlation_id=f"{index:032x}", payload={"api_key": "secret-value"},
            secret="secret-value", request_body="secret-value",
        )
    assert recorder.flush()
    serialized = path.read_text(encoding="utf-8")
    snapshot = json.loads(serialized)
    assert snapshot["truncated"] is True
    assert len(snapshot["events"]) == 2
    assert "secret-value" not in serialized
    assert "payload" not in serialized
    assert "request_body" not in serialized


def test_runtime_result_preserves_diagnostics_and_collection_failure_is_non_authoritative():
    expected = {"schema_version": "minecraft-bridge-diagnostics-summary/1", "actors": {}}
    env = SimpleNamespace(
        get_score=lambda: {}, get_action_log=lambda: {},
        get_eac_audit_artifact=lambda: {}, bridge_cleanup_result={},
        get_minecraft_bridge_diagnostics=lambda: expected,
        agent_iteration_limit=None,
    )
    result = _runtime_result(env)
    assert result["minecraft_bridge_diagnostics"] is expected
    assert result["collection_errors"] == []

    env.get_minecraft_bridge_diagnostics = lambda: (_ for _ in ()).throw(OSError("secret"))
    failed = _runtime_result(env)
    assert failed["collection_errors"] == []
    assert failed["minecraft_bridge_diagnostics"]["diagnostic_collection_error"] == [
        {"error_type": "OSError"}
    ]


def test_runtime_checkpoint_preserves_diagnostics():
    expected = {"schema_version": "minecraft-bridge-diagnostics-summary/1", "actors": {}}
    env = SimpleNamespace(
        get_action_log=lambda: {},
        get_minecraft_bridge_diagnostics=lambda: expected,
    )

    result = _runtime_checkpoint_result(env)

    assert result["minecraft_bridge_diagnostics"] is expected


def test_diagnostics_do_not_change_timeout_or_retry_semantics():
    assert Agent.minecraft_connect_timeout_seconds == 5.0
    assert Agent.minecraft_read_timeout_seconds == 30.0
    error = MinecraftToolTimeoutError(
        "timed out", request_id="c" * 32, timeout_type="read_timeout",
    )
    assert error.failure_detail["outcome_certainty"] == "unknown"
    assert error.failure_detail["retry_safe"] is False
