import json

import yaml

from benchmarks.common.sanitization import (
    REDACTED,
    collect_secret_values,
    redact_command_text,
    sanitize_artifact_value,
    sanitize_command,
)
from benchmarks.experiment_provenance import write_provenance


SENTINEL_SECRET = "sentinel-secret-value-12345"


def test_sanitize_artifact_value_redacts_nested_credentials_and_literal_leaks():
    config = {
        "models": {
            "director": {
                "api_key_env": "OPENAI_API_KEY",
                "api_key": SENTINEL_SECRET,
                "max_tokens": 128,
            }
        },
        "error": f"provider rejected {SENTINEL_SECRET}",
    }

    sanitized = sanitize_artifact_value(config)

    assert sanitized["models"]["director"]["api_key_env"] == "OPENAI_API_KEY"
    assert sanitized["models"]["director"]["api_key"] == REDACTED
    assert sanitized["models"]["director"]["max_tokens"] == 128
    assert sanitized["error"] == f"provider rejected {REDACTED}"


def test_command_sanitizers_hide_flags_and_known_secret_literals():
    secrets = (SENTINEL_SECRET,)

    assert sanitize_command(
        ["python", "run.py", "--api-key", SENTINEL_SECRET, f"--token={SENTINEL_SECRET}"],
        secret_values=secrets,
    ) == ["python", "run.py", "--api-key", REDACTED, f"--token={REDACTED}"]
    assert redact_command_text(
        f"python run.py --api-key {SENTINEL_SECRET}",
        secret_values=secrets,
    ) == f"python run.py --api-key {REDACTED}"


def test_write_provenance_never_persists_resolved_secret(tmp_path):
    config = {
        "models": {
            "director": {
                "api_key_env": "OPENAI_API_KEY",
                "api_key": SENTINEL_SECRET,
            }
        }
    }

    provenance = write_provenance(
        tmp_path,
        benchmark="craft",
        command=f"python run.py --api-key {SENTINEL_SECRET}",
        resolved_config=config,
    )

    assert SENTINEL_SECRET not in provenance["command"]
    assert SENTINEL_SECRET not in (tmp_path / "command.txt").read_text(encoding="utf-8")
    assert SENTINEL_SECRET not in (tmp_path / "config.resolved.json").read_text(encoding="utf-8")
    assert SENTINEL_SECRET not in (tmp_path / "config.resolved.yaml").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "config.resolved.json").read_text())["models"]["director"]["api_key"] == REDACTED
    assert yaml.safe_load((tmp_path / "config.resolved.yaml").read_text())["models"]["director"]["api_key_env"] == "OPENAI_API_KEY"
    assert collect_secret_values(config) == (SENTINEL_SECRET,)
