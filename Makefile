# Mirrors tasks.ps1 for non-Windows shells.
.PHONY: setup fmt lint types test check gen eval report clean

setup:
	uv sync --extra dev

fmt:
	uv run ruff format .

lint:
	uv run ruff check --fix .

types:
	uv run mypy

test:
	uv run pytest

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest

gen:
	uv run reclaimos gen --n 250 --seed 42

eval:
	uv run reclaimos eval --policy all

report:
	uv run reclaimos report

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
