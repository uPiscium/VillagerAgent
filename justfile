set shell := ["bash", "-cu"]

default:
    just --list

validate:
    python -m compileall -q benchmarks/common benchmarks/craft benchmarks/cwah benchmarks/minecraft env

test:
    pytest

check: validate test
