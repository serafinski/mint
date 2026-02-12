.PHONY: test lint format

test:
	uv run python -m pytest tests/ -v

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
