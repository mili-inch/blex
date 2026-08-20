.PHONY: sync test format-check lint typecheck check

sync:
	uv sync --locked --all-groups

test:
	uv run pytest

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run pyrefly check

check: format-check lint typecheck test
