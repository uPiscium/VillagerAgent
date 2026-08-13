import hashlib
import json
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from benchmarks.minecraft.docker_runtime import pinned_runtime_identity
from benchmarks.minecraft.gate_a_v4_readiness_launcher import (
    COMPONENT_FILES,
    COMPONENT_SHA256,
)
from benchmarks.minecraft.matrix_spec import load_matrix_spec
from env.runtime_execution import RuntimeExecution


EXECUTION_ROOT = Path(
    "/home/upiscium/Documents/Research/VillagerAgent/.worktrees/issue-506-execution-2511366"
)
PREMANIFEST = Path("/tmp/opencode/issue-506-v4-25113661-private/premanifest.json")


def test_frozen_execution_identity_recomputes_exactly():
    root = Path(__file__).resolve().parents[1]
    revision = "25113661a6b09761ab47a05bd70bd8f0386e2b67"
    with tempfile.TemporaryDirectory(prefix="issue507-frozen-") as temporary:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", revision], cwd=root,
            check=True, capture_output=True,
        ).stdout
        checkout = Path(temporary) / "checkout"
        checkout.mkdir()
        with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as stream:
            stream.extractall(checkout, filter="data")
        execution = RuntimeExecution.resolve(checkout)
        execution.verify()
        assert execution.manifest_sha256 == (
            "ce8c30e13ddef9251d64a3f833625e509dd9590b163229f52fe585444794ae5d"
        )
        assert len(execution.assets) == 125
        assert pinned_runtime_identity(execution)["digest"] == (
            "sha256:25441b6e08ce2eff2a71dd6330ff4ddfaa6e5c9f1aa89e508e2580a16b262e0f"
        )


def test_frozen_premanifest_identity_recomputes_exactly():
    if not PREMANIFEST.is_file() or not EXECUTION_ROOT.is_dir():
        pytest.skip("intended-host frozen premanifest is not installed")
    raw = PREMANIFEST.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "222afe434cace4e7609cddaae578284ba1d2a1b1ed0dd927a4a6155ade71192f"
    )
    assert stat.S_IMODE(PREMANIFEST.stat().st_mode) == 0o444
    value = json.loads(raw)
    assert value["premanifest_sha256"] == (
        "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a"
    )
    assert load_matrix_spec(PREMANIFEST, repo_root=EXECUTION_ROOT).matrix_id == (
        "minecraft-judged-production-v4"
    )


def test_readiness_component_hashes_match_current_bytes():
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "minecraft"
    observed = {
        name: hashlib.sha256((root / COMPONENT_FILES[name]).read_bytes()).hexdigest()
        for name in COMPONENT_FILES
    }
    assert observed == COMPONENT_SHA256
