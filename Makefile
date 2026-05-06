.PHONY: check lint fmt test smoke install

install:
	pip install -e ".[dev]"

check: lint test smoke

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .

test:
	pytest -q

smoke:
	scrape-gateway --help
	python examples/basic.py
