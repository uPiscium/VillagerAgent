import pytest
from types import SimpleNamespace
from benchmarks.minecraft import gate_a_v4_real_adapter as adapter


def test_public_factory_requires_external_authorization():
    with pytest.raises(TypeError, match="run composition authority required"):
        adapter.bind_fixed_adapter(object(), object())
    with pytest.raises(TypeError, match="minting is unavailable"):
        adapter.GateARunCompositionAuthority()
    assert not {"ExecutionPermit", "AdapterPermit"}.intersection(vars(adapter))


def test_no_module_authority_sentinel():
    assert "_BIND_AUTHORITY" not in vars(adapter)


def test_runtime_modules_reject_executor_or_validator_hash_drift(monkeypatch, tmp_path):
    loader = SimpleNamespace(authenticated_source_sha256=lambda name: "0" * 64)
    modules = adapter.RuntimeModules(*(
        SimpleNamespace(__file__=str(adapter.FIXED_EXECUTION_ROOT / relative),
                        __loader__=loader, __spec__=SimpleNamespace(loader=loader))
        for relative in adapter.EXPECTED_ORIGINS.values()
    ))
    monkeypatch.setattr(adapter, "_sha256", lambda path: "0" * 64)
    with pytest.raises(adapter.RealAdapterError, match="implementation hash rejected"):
        modules.verify()


def test_runtime_modules_reject_spoofed_file_without_authenticated_loader():
    modules = adapter.RuntimeModules(*(
        SimpleNamespace(__file__=str(adapter.FIXED_EXECUTION_ROOT / relative),
                        __loader__=None, __spec__=SimpleNamespace(loader=None))
        for relative in adapter.EXPECTED_ORIGINS.values()
    ))
    with pytest.raises(adapter.RealAdapterError, match="loader rejected"):
        modules.verify()
