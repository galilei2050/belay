SHELL := /bin/bash

.PHONY: setup
setup:
	pip install -e .[dev]
	pre-commit install

.PHONY: lint
lint:
	ruff format --check .
	ruff check .

.PHONY: lint-fix
lint-fix:
	ruff format .
	ruff check . --fix

.PHONY: typecheck
typecheck:
	mypy plugins/

.PHONY: test
test:
	pytest

.PHONY: ci
ci: lint typecheck test
