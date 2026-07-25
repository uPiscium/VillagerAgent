set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks env model pipeline type_define
    python -m benchmarks.common.publish_bundle check-docs

test:
    pytest

partnr-smoke:
    python -m benchmarks.partnr.smoke --output result/partnr/fixture_smoke.json

partnr-real-preflight:
    "${PARTNR_PYTHON:-python}" -m benchmarks.partnr.real_smoke --mode preflight --output result/partnr/real_preflight.json

partnr-step-zero:
    "${PARTNR_PYTHON:-python}" -m benchmarks.partnr.real_smoke --mode step-zero --require-ready --output result/partnr/step_zero_gate.json

partnr-bounded-smoke:
    "${PARTNR_PYTHON:-python}" -m benchmarks.partnr.real_smoke --mode bounded --require-ready --output result/partnr/bounded_gate.json

partnr-evidence-bundle:
    python -m benchmarks.partnr.evidence_bundle --output result/partnr/issue_378_evidence --overwrite

tdw-mat-smoke:
    python -m benchmarks.tdw_mat.smoke --output result/tdw_mat/fixture_smoke.json

tdw-mat-comparison:
    python -m benchmarks.tdw_mat.comparison --output result/tdw_mat/fixture_comparison.json

tdw-mat-real-preflight:
    python -m benchmarks.tdw_mat.real_smoke --preflight-only --output result/tdw_mat/real_preflight.json

tdw-mat-real-smoke:
    python -m benchmarks.tdw_mat.real_smoke --require-ready --output result/tdw_mat/real_smoke.json

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

visualizer-fixture:
    just visualizer-dev visualizer/fixtures/runs

visualizer-serve result_root="result": visualizer-frontend-build
    env -u VIRTUAL_ENV uv run --project visualizer/backend python -m villageragent_visualizer --result-root "{{result_root}}" --frontend-dist visualizer/frontend/dist

visualizer-check: visualizer-backend-test visualizer-frontend-test visualizer-frontend-build
