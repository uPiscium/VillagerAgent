set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks env model pipeline type_define

test:
    pytest

real-smoke:
    pytest -q tests/test_minecraft_real_smoke.py -m real_smoke -rs

real-smoke-ollama:
    pytest -q tests/test_minecraft_real_smoke.py -m real_smoke -k ollama -rs

real-smoke-port:
    pytest -q tests/test_minecraft_real_smoke.py -m real_smoke -k port -rs

real-smoke-bridge:
    pytest -q tests/test_minecraft_real_smoke.py -m real_smoke -k bridge -rs

real-smoke-judged:
    pytest -q tests/test_minecraft_real_smoke.py -m real_smoke -k judged -rs

check: validate test
