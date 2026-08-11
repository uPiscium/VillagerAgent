import hashlib
import json
import subprocess
import sys
from pathlib import Path

from benchmarks.minecraft import gate_a_v3_execute_once_launcher as launcher


def _run_direct(*args):
    return subprocess.run(
        [sys.executable, "-I", "-B", str(Path(launcher.__file__)), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_direct_unanchored_invocation_rejects_without_effects():
    result = _run_direct()
    record = json.loads(result.stdout)

    assert result.returncode == 3
    assert record["reason_code"] == "launcher_authentication_failed"
    assert record["attempt_consumed"] is False
    assert record["judged_attempts"] == 0
    assert all(value == 0 for value in record["counters"].values())
    assert record["execution_flags"] == {
        "canary": False,
        "five_run": False,
        "matrix": False,
        "production": False,
    }


def test_open_once_sha_authenticated_invocation_is_consumed_disabled():
    path = Path(launcher.__file__)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    command = (
        "import hashlib,os;"
        f"path={str(path)!r};expected={digest!r};"
        "fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);"
        "stream=os.fdopen(fd,'rb');source=stream.read();stream.close();"
        "assert hashlib.sha256(source).hexdigest()==expected;"
        "ns={'__name__':'__main__','__file__':path,'ISSUE502_AUTHENTICATED_LAUNCHER_SHA256':expected};"
        "exec(compile(source,path,'exec'),ns,ns)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    record = json.loads(result.stdout)

    assert result.returncode == 3
    assert record["reason_code"] == "consumed_v3_execution_disabled"
    assert record["attempt_consumed"] is False
    assert record["judged_attempts"] == 0
    assert record["execution_flags"] == {
        "canary": False,
        "five_run": False,
        "matrix": False,
        "production": False,
    }
