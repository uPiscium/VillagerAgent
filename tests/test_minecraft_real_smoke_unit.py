import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.common.run_artifacts import read_attempt_id, validate_run_attempt
from benchmarks.minecraft.real_smoke import (
    _ProcessIdentity,
    _bridge_child,
    _capture_process_identity,
    _cleanup_identity_process,
    _collect_session_processes,
    _resolve_process_identity,
    _stop_internal_javascript_bridge,
    _stop_process,
    main,
    run_bridge_smoke,
    run_judged_smoke,
    run_ollama_preflight,
    run_port_preflight,
)


@pytest.fixture(autouse=True)
def _isolate_minecraft_lock_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VILLAGER_MINECRAFT_LOCK_ROOT", str(tmp_path / "target-locks"))


def test_real_smoke_is_disabled_without_explicit_opt_in(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("VILLAGER_OLLAMA_REAL_SMOKE", raising=False)

    assert main(["ollama", "--output-dir", str(tmp_path)]) == 0
    assert "SKIP ollama: set VILLAGER_OLLAMA_REAL_SMOKE=1 to opt in" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_ollama_preflight_records_digest_and_valid_attempt(monkeypatch, tmp_path):
    class OllamaHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/tags"
            payload = json.dumps({
                "models": [{"name": "smoke:latest", "digest": "sha256:immutable"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("OLLAMA_API_BASE", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "smoke:latest")
    try:
        result = run_ollama_preflight(output_root=tmp_path, timeout_seconds=2, overwrite=False)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    run_dir = tmp_path / "ollama_preflight"
    assert result["model_digest"] == "sha256:immutable"
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    model = next(asset for asset in provenance["assets"] if asset["name"] == "runtime_model")
    assert model["digest"] == "sha256:immutable"
    _assert_recorded_command(run_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "ollama",
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "2.0",
    ])
    validate_run_attempt(run_dir, attempt_id=read_attempt_id(run_dir))


def test_minecraft_port_preflight_records_reachable_endpoint(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        result = run_port_preflight(
            output_root=tmp_path,
            host="127.0.0.1",
            port=port,
            timeout_seconds=2,
            overwrite=True,
        )
    finally:
        server.close()

    run_dir = tmp_path / "minecraft_port"
    assert result["reachable"] is True
    assert result["host"] == "127.0.0.1"
    assert result["port"] == port
    _assert_recorded_command(run_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "port",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "2.0",
        "--overwrite",
    ])
    validate_run_attempt(run_dir, attempt_id=read_attempt_id(run_dir))


def test_bridge_records_exact_runnable_command(monkeypatch, tmp_path):
    cleanup_calls = []

    class Process:
        pid = 123
        returncode = 0

        def __init__(self, command):
            result_path = Path(command[command.index("--result-path") + 1])
            result_path.write_text(json.dumps({"bridge_ping": {"status": True}}), encoding="utf-8")

        def communicate(self, timeout):
            return "", ""

    real_popen = __import__("subprocess").Popen
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda command, **kwargs: (
            Process(command) if "--result-path" in command else real_popen(command, **kwargs)
        ),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._capture_process_identity",
        lambda pid: _ProcessIdentity(pid=pid, create_time=1.0),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._stop_process",
        lambda process, **kwargs: cleanup_calls.append(process) or True,
    )

    run_bridge_smoke(
        output_root=tmp_path,
        host="minecraft.example.test",
        port=25565,
        timeout_seconds=7,
        overwrite=True,
    )

    _assert_recorded_command(tmp_path / "minecraft_bridge", [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "bridge",
        "--host",
        "minecraft.example.test",
        "--port",
        "25565",
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "7.0",
        "--overwrite",
    ])
    assert len(cleanup_calls) == 1


def test_bridge_rejects_normal_result_when_final_session_cleanup_is_incomplete(monkeypatch, tmp_path):
    class Process:
        pid = 123
        returncode = 0

        def __init__(self, command):
            result_path = Path(command[command.index("--result-path") + 1])
            result_path.write_text(json.dumps({"bridge_ping": {"status": True}}), encoding="utf-8")

        def communicate(self, timeout):
            return "", ""

    real_popen = subprocess.Popen
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda command, **kwargs: Process(command) if "_bridge-child" in command else real_popen(command, **kwargs),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._capture_process_identity",
        lambda pid: _ProcessIdentity(pid=pid, create_time=1.0),
    )
    monkeypatch.setattr("benchmarks.minecraft.real_smoke._stop_process", lambda process, **kwargs: False)

    with pytest.raises(RuntimeError, match="final cleanup left verified session members alive"):
        run_bridge_smoke(
            output_root=tmp_path,
            host="127.0.0.1",
            port=25565,
            timeout_seconds=2,
            overwrite=False,
        )


def test_bridge_fast_exit_with_identity_capture_failure_records_cleanup_failure(monkeypatch, tmp_path):
    class Process:
        pid = 123
        returncode = 0

        def __init__(self, command):
            result_path = Path(command[command.index("--result-path") + 1])
            result_path.write_text(json.dumps({"bridge_ping": {"status": True}}), encoding="utf-8")

        def communicate(self, timeout):
            return "", ""

        def poll(self):
            return 0

        def terminate(self):
            pytest.fail("an exited child with unavailable identity must not be signaled")

    real_popen = subprocess.Popen
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda command, **kwargs: Process(command) if "_bridge-child" in command else real_popen(command, **kwargs),
    )
    monkeypatch.setattr("benchmarks.minecraft.real_smoke._capture_process_identity", lambda pid: None)

    with pytest.raises(RuntimeError, match="final cleanup incomplete: child identity unavailable"):
        run_bridge_smoke(
            output_root=tmp_path,
            host="127.0.0.1",
            port=25565,
            timeout_seconds=2,
            overwrite=False,
        )

    verification = json.loads(
        (tmp_path / "minecraft_bridge" / "verification.json").read_text(encoding="utf-8")
    )
    assert verification["status"] == "failure"
    assert verification["error_type"] == "RuntimeError"
    assert verification["error"] == (
        "env_type.none bridge smoke final cleanup incomplete: child identity unavailable"
    )


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf"])
def test_environment_timeout_must_be_finite_and_positive(timeout, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VILLAGER_MINECRAFT_PORT_SMOKE", "1")
    monkeypatch.setenv("VILLAGER_REAL_SMOKE_TIMEOUT_SECONDS", timeout)
    monkeypatch.setenv("MINECRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("MINECRAFT_PORT", "25565")
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.run_port_preflight",
        lambda **kwargs: pytest.fail("invalid timeout must be rejected before the check starts"),
    )

    assert main(["port", "--output-dir", str(tmp_path)]) == 2
    assert "timeout must be a finite positive number" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_bridge_rejects_invalid_timeout_before_process_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("invalid timeout must be rejected before process launch"),
    )

    with pytest.raises(ValueError, match="timeout must be a finite positive number"):
        run_bridge_smoke(
            output_root=tmp_path,
            host="127.0.0.1",
            port=25565,
            timeout_seconds=float("nan"),
            overwrite=False,
        )

    assert not list(tmp_path.iterdir())


def test_bridge_timeout_reports_endpoint_and_phase(monkeypatch, tmp_path):
    class TimedOutProcess:
        pid = 123

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("bridge-child", timeout)

    real_popen = subprocess.Popen
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda command, **kwargs: (
            TimedOutProcess() if "_bridge-child" in command else real_popen(command, **kwargs)
        ),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._capture_process_identity",
        lambda pid: _ProcessIdentity(pid=pid, create_time=1.0),
    )
    monkeypatch.setattr("benchmarks.minecraft.real_smoke._stop_process", lambda process, **kwargs: True)

    with pytest.raises(
        TimeoutError,
        match=r"bridge smoke to 127\.0\.0\.1:40000 timed out during starting bridge child after 0\.1 seconds",
    ):
        run_bridge_smoke(
            output_root=tmp_path,
            host="127.0.0.1",
            port=40000,
            timeout_seconds=0.1,
            overwrite=False,
        )


def test_bridge_child_prepares_runtime_directories_before_environment_init(monkeypatch, tmp_path):
    import env.env as env_module

    class Environment:
        running = False

        def __init__(self, *args, **kwargs):
            assert Path(".cache").is_dir()
            assert Path("data/history").is_dir()

        def agent_register(self, **kwargs):
            return None

        def stop(self):
            pytest.fail("bridge cleanup must not use Environment.stop Popen signaling")

    class Agent:
        agent_process = {}

        @staticmethod
        def launch(**kwargs):
            return None

        @staticmethod
        def ping(agent_name):
            return {"status": True}

        @staticmethod
        def get_environment_info_dict(agent_name):
            return {"status": True}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module, "VillagerBench", Environment)
    monkeypatch.setattr(env_module, "Agent", Agent)
    args = SimpleNamespace(
        phase_path=str(tmp_path / "phase.txt"),
        result_path=str(tmp_path / "result.json"),
        host="127.0.0.1",
        port=25565,
        local_port=5000,
        agent_name="Alice",
        world="world",
    )

    assert _bridge_child(args) == 0
    assert (tmp_path / ".cache").is_dir()
    assert (tmp_path / "phase.txt").read_text(encoding="utf-8").strip() == "bridge cleanup complete"


def test_internal_bridge_child_exit_stops_inherited_pipe_holder_and_ignores_non_daemon_thread(tmp_path):
    marker_path = tmp_path / "child-complete.txt"
    code = (
        "import subprocess, sys, threading, time, types\n"
        "from pathlib import Path\n"
        "import benchmarks.minecraft.real_smoke as smoke\n"
        "old_identity = smoke._capture_internal_javascript_identity()\n"
        "smoke._stop_internal_javascript_bridge(None, old_identity)\n"
        "holder = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "smoke.sys.modules['javascript.connection'] = types.SimpleNamespace(proc=holder)\n"
        "def child(args):\n"
        "    threading.Thread(target=time.sleep, args=(60,), daemon=False).start()\n"
        f"    Path({str(marker_path)!r}).write_text('closed', encoding='utf-8')\n"
        "    print('bridge child complete')\n"
        "    return 0\n"
        "smoke._bridge_child = child\n"
        "smoke._exit_bridge_child(None)\n"
    )

    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0
    assert time.monotonic() - started < 5
    assert completed.stdout.strip() == "bridge child complete"
    assert marker_path.read_text(encoding="utf-8") == "closed"


def test_internal_bridge_child_cleanup_exceptions_still_force_nonzero_exit():
    code = (
        "import types\n"
        "import benchmarks.minecraft.real_smoke as smoke\n"
        "old_identity = smoke._capture_internal_javascript_identity()\n"
        "smoke._stop_internal_javascript_bridge(None, old_identity)\n"
        "smoke.sys.modules['javascript.connection'] = types.SimpleNamespace(proc=None)\n"
        "def fail_capture():\n"
        "    raise RuntimeError('capture failed')\n"
        "def fail_cleanup(args, identity):\n"
        "    raise RuntimeError('cleanup failed')\n"
        "smoke._capture_internal_javascript_identity = fail_capture\n"
        "smoke._stop_internal_javascript_bridge = fail_cleanup\n"
        "smoke._bridge_child = lambda args: 0\n"
        "smoke._exit_bridge_child(None)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 1
    assert "initial JavaScript identity capture failed" in completed.stderr
    assert "internal JavaScript cleanup failed" in completed.stderr


def test_identity_cleanup_kills_stalled_verified_survivor(monkeypatch, tmp_path):
    calls = []

    class Process:
        pid = 123

        def create_time(self):
            return 1.0

        def is_running(self):
            return True

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

    process = Process()
    phase_path = tmp_path / "phase.txt"
    waits = iter([[process], [process]])
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._collect_process_tree",
        lambda identity: [process],
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._wait_processes",
        lambda processes, timeout_seconds: next(waits),
    )

    complete = _cleanup_identity_process(
        _ProcessIdentity(pid=123, create_time=1.0),
        label="bridge agent process Alice",
        phase_path=phase_path,
    )

    assert complete is False
    assert calls == ["terminate", "kill"]
    assert phase_path.read_text(encoding="utf-8").strip() == "bridge agent process Alice survived kill"


def test_internal_node_cleanup_rejects_reused_pid(monkeypatch, tmp_path):
    class ReusedProcess:
        pid = 123

        def create_time(self):
            return 2.0

    monkeypatch.setattr("benchmarks.minecraft.real_smoke.psutil.Process", lambda pid: ReusedProcess())
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._signal_processes",
        lambda *args, **kwargs: pytest.fail("reused PID must not be signaled"),
    )
    phase_path = tmp_path / "phase.txt"

    _stop_internal_javascript_bridge(
        SimpleNamespace(phase_path=str(phase_path)),
        _ProcessIdentity(pid=123, create_time=1.0),
    )

    assert phase_path.read_text(encoding="utf-8").strip() == "internal JavaScript bridge already stopped"


def test_parent_fallback_post_kill_wait_is_bounded(monkeypatch):
    class Process:
        pid = 123

        def __init__(self):
            self.calls = []

        def poll(self):
            return None

        def terminate(self):
            self.calls.append("terminate")

        def kill(self):
            self.calls.append("kill")

        def wait(self, timeout):
            self.calls.append(("wait", timeout))
            raise subprocess.TimeoutExpired("bridge-child", timeout)

    process = Process()
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._capture_process_identity",
        lambda pid: None,
    )

    assert _stop_process(process) is False

    assert process.calls == ["terminate", ("wait", 2), "kill", ("wait", 2)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX session regression")
def test_bridge_timeout_stops_orphaned_descendant_after_leader_exits(tmp_path):
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_code = (
        "import os, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(descendant_pid_path)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    leader_identity = _capture_process_identity(process.pid)
    descendant_identity = None

    try:
        assert leader_identity is not None
        descendant_identity = _capture_process_identity(_wait_for_pid(descendant_pid_path))
        assert descendant_identity is not None
        process.wait(timeout=2)
        assert _resolve_process_identity(leader_identity) is None

        assert _stop_process(process, leader_identity=leader_identity) is True

        assert _resolve_process_identity(descendant_identity) is None
        assert _collect_session_processes(leader_identity) == []
    finally:
        if descendant_identity is not None:
            remaining = _resolve_process_identity(descendant_identity)
            if remaining is not None:
                remaining.kill()
                remaining.wait(timeout=2)


def test_session_collection_rejects_reused_leader_identity(monkeypatch):
    class ReusedLeader:
        pid = 123

        def create_time(self):
            return 200.0

    monkeypatch.setattr("benchmarks.minecraft.real_smoke.psutil.Process", lambda pid: ReusedLeader())
    monkeypatch.setattr("benchmarks.minecraft.real_smoke.os.getsid", lambda pid: pid)
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.psutil.process_iter",
        lambda *args, **kwargs: pytest.fail("a reused session must not be enumerated"),
    )

    assert _collect_session_processes(_ProcessIdentity(pid=123, create_time=100.0)) == []


def test_parent_cleanup_kills_late_session_descendant_and_rescans_after_kill(monkeypatch):
    calls = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return float(self.pid)

        def is_running(self):
            return True

        def terminate(self):
            calls.append(("terminate", self.pid))

        def kill(self):
            calls.append(("kill", self.pid))

    leader = Process(1)
    late_descendant = Process(2)
    scans = iter([[leader], [late_descendant], []])
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._collect_session_processes",
        lambda identity: next(scans),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._wait_processes",
        lambda processes, timeout_seconds: [],
    )

    complete = _stop_process(
        object(),
        leader_identity=_ProcessIdentity(pid=1, create_time=1.0),
        posix=True,
    )

    assert complete is True
    assert calls == [("terminate", 1), ("kill", 2)]


def test_non_posix_cleanup_terminates_descendants_before_parent_and_escalates(monkeypatch):
    calls = []

    class Process:
        def __init__(self, pid, children=()):
            self.pid = pid
            self._children = list(children)
            self.running = True

        def create_time(self):
            return float(self.pid)

        def is_running(self):
            return self.running

        def children(self, recursive):
            assert recursive is True
            return self._children

        def terminate(self):
            calls.append(("terminate", self.pid))

        def kill(self):
            calls.append(("kill", self.pid))

    child = Process(2)
    leader = Process(1, children=[child])
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._resolve_process_identity",
        lambda identity: leader,
    )
    waits = iter([[child], []])
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke._wait_processes",
        lambda processes, timeout_seconds: next(waits),
    )

    _stop_process(
        object(),
        leader_identity=_ProcessIdentity(pid=1, create_time=1.0),
        posix=False,
    )

    assert calls == [("terminate", 2), ("terminate", 1), ("kill", 2)]


def test_ollama_preflight_redacts_url_credential_sentinels(monkeypatch, tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    response = Response()
    monkeypatch.setenv(
        "OLLAMA_API_BASE",
        "http://USER_SENTINEL:PASSWORD_SENTINEL@example.test/v1?token=QUERY_SENTINEL",
    )
    monkeypatch.setenv("OLLAMA_MODEL", "smoke:latest")
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.urlopen",
        lambda request, timeout: response,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.json.load",
        lambda stream: {"models": [{"name": "smoke:latest", "digest": "sha256:immutable"}]},
    )

    result = run_ollama_preflight(output_root=tmp_path, timeout_seconds=2, overwrite=False)

    assert result["api_base"] == "http://example.test/v1"
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "ollama_preflight").rglob("*")
        if path.is_file()
    )
    for sentinel in ("USER_SENTINEL", "PASSWORD_SENTINEL", "QUERY_SENTINEL"):
        assert sentinel not in artifact_text


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config.update(agent_num=2), "agent_num=1"),
        (lambda config: config.pop("evaluation_arg"), "scenario evaluation_arg"),
        (lambda config: config.pop("reset_snapshot_path"), "reset/world identity path"),
        (lambda config: config.pop("bridge_path"), "bridge identity path"),
        (lambda config: config.pop("server_version"), "server_version and server_protocol"),
    ],
)
def test_judged_smoke_rejects_missing_safety_prerequisites(tmp_path, mutate, message):
    config_path = _write_judged_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mutate(config)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        run_judged_smoke(
            output_root=tmp_path / "output",
            config_path=config_path,
            timeout_seconds=10,
            overwrite=False,
        )

    assert not (tmp_path / "output").exists()


def test_judged_smoke_checks_reachability_before_mutation(monkeypatch, tmp_path):
    config_path = _write_judged_config(tmp_path)
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.socket.create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError("closed")),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.run_minecraft_experiment",
        lambda **kwargs: pytest.fail("unreachable server must not launch the experiment"),
    )

    with pytest.raises(ConnectionRefusedError, match="closed"):
        run_judged_smoke(
            output_root=tmp_path / "output",
            config_path=config_path,
            timeout_seconds=10,
            overwrite=False,
        )

    parent_dir = tmp_path / "output" / "minecraft_judged_smoke"
    manifest = validate_run_attempt(
        parent_dir,
        attempt_id=read_attempt_id(parent_dir),
        require_completed=False,
    )
    assert manifest["status"] == "failed"
    assert not (parent_dir / "_COMPLETED").exists()


def test_judged_missing_score_finalizes_parent_attempt_as_failure(monkeypatch, tmp_path):
    config_path = _write_judged_config(tmp_path)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.socket.create_connection",
        lambda *args, **kwargs: Connection(),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: {"score": {}, "action_log": {}},
    )

    summary = run_judged_smoke(
        output_root=tmp_path / "output",
        config_path=config_path,
        timeout_seconds=10,
        overwrite=False,
    )

    assert summary["score_available"] is False
    parent_dir = tmp_path / "output" / "minecraft_judged_smoke"
    manifest = validate_run_attempt(
        parent_dir,
        attempt_id=read_attempt_id(parent_dir),
        require_completed=False,
    )
    provenance = json.loads((parent_dir / "provenance.json").read_text(encoding="utf-8"))
    verification = json.loads((parent_dir / "verification.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert provenance["lifecycle"]["status"] == "failure"
    assert verification["status"] == "failure"
    assert not (parent_dir / "_COMPLETED").exists()
    _assert_recorded_command(parent_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "judged",
        "--config",
        str(config_path),
        "--output-dir",
        str(tmp_path / "output"),
        "--timeout-seconds",
        "10.0",
    ])
    experiment_dir = tmp_path / "output" / "minecraft_judged_meta"
    experiment_manifest = validate_run_attempt(
        experiment_dir,
        attempt_id=read_attempt_id(experiment_dir),
        require_completed=False,
    )
    assert experiment_manifest["status"] == "failed"


def _assert_recorded_command(run_dir: Path, expected: list[str]) -> None:
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["argv"] == expected
    assert (run_dir / "command.txt").read_text(encoding="utf-8") == shlex.join(expected) + "\n"


def _wait_for_pid(path: Path, timeout: float = 2) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return int(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {path}")


def _write_judged_config(tmp_path: Path) -> Path:
    reset_path = tmp_path / "world-reset"
    bridge_path = tmp_path / "bridge"
    reset_path.mkdir()
    bridge_path.mkdir()
    config_path = tmp_path / "judged.json"
    config_path.write_text(json.dumps({
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": "Move to the target",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": "judged-smoke",
        "task_scenario": "move",
        "evaluation_arg": {"target": [1, 2, 3]},
        "reset_snapshot_path": str(reset_path),
        "bridge_path": str(bridge_path),
        "server_version": "1.19.2",
        "server_protocol": "760",
    }), encoding="utf-8")
    return config_path
