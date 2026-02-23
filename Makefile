.PHONY: test lint format build-sandbox

test:
	uv run python -m pytest tests/ -v

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

build-sandbox:
	docker build -t jit-sandbox sandbox/
