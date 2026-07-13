set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks env model pipeline type_define

test:
    pytest

check: validate test
