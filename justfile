set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks env model pipeline type_define
    python -m benchmarks.common.publish_bundle check-docs

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

visualizer-backend-dev *args:
    env -u VIRTUAL_ENV uv run --project visualizer/backend python -m villageragent_visualizer {{args}}

visualizer-backend-test:
    env -u VIRTUAL_ENV uv run --project visualizer/backend --extra dev pytest visualizer/backend/tests

visualizer-frontend-dev:
    npm run --prefix visualizer/frontend dev

visualizer-frontend-test:
    npm run --prefix visualizer/frontend test

visualizer-frontend-build:
    npm run --prefix visualizer/frontend build

visualizer-dev result_root="result":
    #!/usr/bin/env bash
    set -euo pipefail
    env -u VIRTUAL_ENV uv run --project visualizer/backend python -m villageragent_visualizer --result-root "{{result_root}}" &
    backend_pid=$!
    npm run --prefix visualizer/frontend dev &
    frontend_pid=$!
    trap 'kill "$backend_pid" "$frontend_pid" 2>/dev/null || true' EXIT INT TERM
    wait -n "$backend_pid" "$frontend_pid"

visualizer-check: visualizer-backend-test visualizer-frontend-test visualizer-frontend-build
