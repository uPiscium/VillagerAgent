import hashlib
from pathlib import Path
from benchmarks.minecraft import gate_a_v4_readiness_bootstrap as bootstrap


def test_pinned_launcher_hash_matches_actual_bytes():
    launcher = Path(bootstrap.__file__).with_name("gate_a_v4_readiness_launcher.py")
    assert bootstrap.READINESS_LAUNCHER_SHA256 != "__FINALIZED_LAUNCHER_SHA256__"
    assert hashlib.sha256(launcher.read_bytes()).hexdigest() == bootstrap.READINESS_LAUNCHER_SHA256
