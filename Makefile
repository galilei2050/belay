SHELL := /bin/bash

.PHONY: setup
setup:
	uv sync --group dev
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

.PHONY: lint
lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run python anon_lint.py --recursive plugins/

.PHONY: lint-fix
lint-fix:
	uv run ruff format .
	uv run ruff check . --fix
	uv run python anon_lint.py --recursive plugins/

.PHONY: typecheck
typecheck:
	uv run mypy plugins/

.PHONY: test
test:
	uv run pytest

.PHONY: pre-commit
pre-commit: lint-fix

.PHONY: pre-push
pre-push: typecheck test

.PHONY: ci
ci: lint typecheck test
