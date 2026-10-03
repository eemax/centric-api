set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

sync:
    uv sync --locked

format:
    uv run ruff format

format-check:
    uv run ruff format --check

lint:
    uv run ruff check

test:
    uv run pytest

smoke:
    uv run centric-api --help >/dev/null

check: format-check lint smoke test

build:
    uv build
