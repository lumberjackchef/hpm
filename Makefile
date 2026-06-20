.PHONY: all install dev lint typecheck build test clean

PYTHON ?= python3

all: install lint typecheck test

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check src/

typecheck:
	mypy src/

build:
	$(PYTHON) -m build

test:
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf dist/ build/ *.egg-info/ __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
