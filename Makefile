.PHONY: install test lint typecheck fmt check build clean venv help

# ── Setup ─────────────────────────────────────────────────────────────────────
install:          ## Install in editable mode with dev dependencies
	pip install -e ".[dev]" --break-system-packages

venv:             ## Create a virtual environment and install
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@echo "Activate with: source .venv/bin/activate"

# ── Quality gates ─────────────────────────────────────────────────────────────
test:             ## Run all tests
	pytest tests/ -q

test-v:           ## Run tests with verbose output
	pytest tests/ -v --tb=short

lint:             ## Check code style with ruff
	ruff check meshflow/

fmt:              ## Format and fix code with ruff
	ruff check meshflow/ --fix
	ruff format meshflow/

typecheck:        ## Run mypy type checker
	mypy meshflow/ --ignore-missing-imports

check: lint typecheck test  ## Run all quality checks

# ── Build ─────────────────────────────────────────────────────────────────────
build:            ## Build wheel and sdist
	hatch build

clean:            ## Remove build artifacts
	rm -rf dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── Run examples ─────────────────────────────────────────────────────────────
run-quickstart:   ## Run simulated quickstart (no API key needed)
	python examples/quickstart.py

run-live:         ## Run live quickstart (needs ANTHROPIC_API_KEY in .env)
	python examples/live_quickstart.py

# ── Help ──────────────────────────────────────────────────────────────────────
help:             ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
