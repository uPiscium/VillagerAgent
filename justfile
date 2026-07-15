set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks env model pipeline type_define
    python -m benchmarks.common.publish_bundle check-docs

test:
    pytest

check: validate test
