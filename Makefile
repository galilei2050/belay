SHELL := /bin/bash

.PHONY: setup
setup:
	uv sync --group dev
	uv run pre-commit install

.PHONY: lint
lint:
	uv run ruff format --check .
	uv run ruff check .

.PHONY: lint-fix
lint-fix:
	uv run ruff format .
	uv run ruff check . --fix

.PHONY: typecheck
typecheck:
	uv run mypy plugins/

.PHONY: test
test:
	uv run pytest

.PHONY: ci
ci: lint typecheck test
