set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

sync:
    uv sync --locked

install-hooks:
    git config core.hooksPath scripts/git-hooks

format:
    uv run ruff format

format-check:
    uv run ruff format --check

lint:
    uv run ruff check

compile:
    uv run python -m compileall -q src/centric_api

test:
    uv run pytest

smoke:
    uv run centric-api --help >/dev/null

check: format-check lint compile smoke test

test-file path:
    uv run pytest {{path}}

loc:
    @git ls-files | while IFS= read -r f; do \
      [ -f "$f" ] || continue; \
      case "$f" in \
        *.py|*.html|*.yml|*.yaml|*.md|*.toml|*.json|*.js|*.ts|*.css) \
          wc -l < "$f" | awk -v file="$f" '{print $1 "\t" file}' ;; \
      esac; \
    done | sort -nr | head -25
