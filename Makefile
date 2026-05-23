.PHONY: install test lint typecheck fmt check build clean venv run-quickstart run-live run-cross-framework run-multi-framework run-supervisor run-topoprior docker-build docker-run docker-push k8s-apply bench bench-fast dashboard test-live help

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy

# ── Setup ─────────────────────────────────────────────────────────────────────
install:          ## Install in editable mode with dev dependencies
	$(PIP) install -e ".[dev]"

venv:             ## Create a virtual environment and install
	python3 -m venv .venv
	$(PIP) install -e ".[dev]"
	@echo "Activate with: source .venv/bin/activate"

# ── Quality gates ─────────────────────────────────────────────────────────────
test:             ## Run all tests
	$(PYTEST) tests/ -q

test-v:           ## Run tests with verbose output
	$(PYTEST) tests/ -v --tb=short

lint:             ## Check code style with ruff
	$(RUFF) check meshflow/ tests/ examples/legal_critical_contract_review.py

fmt:              ## Format and fix code with ruff
	$(RUFF) check meshflow/ tests/ examples/legal_critical_contract_review.py --fix
	$(RUFF) format meshflow/ tests/ examples/legal_critical_contract_review.py

typecheck:        ## Run mypy type checker
	$(MYPY) meshflow/ --ignore-missing-imports

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

run-cross-framework: ## Run cross-framework demo (no API key — uses simulated adapters)
	$(PYTHON) examples/cross_framework_demo.py

run-multi-framework: ## Run multi-framework demo
	$(PYTHON) examples/multi_framework.py

run-hipaa:        ## Run HIPAA PHI pipeline demo (simulated, no API key needed)
	$(PYTHON) examples/hipaa_phi_pipeline.py

run-regulated:    ## Run regulated financial review demo (simulated, no API key needed)
	$(PYTHON) examples/regulated_financial_review.py

run-legal-critical: ## Run legal-critical NDA review demo (simulated, no API key needed)
	$(PYTHON) examples/legal_critical_nda_review.py

# ── Live tests ───────────────────────────────────────────────────────────────
test-live:        ## Run live LLM tests (needs ANTHROPIC_API_KEY)
	$(PYTEST) tests/test_live.py -v --tb=short -m live

test-live-slow:   ## Run live tests including slow multi-turn tests
	MESHFLOW_LIVE_SLOW=1 $(PYTEST) tests/test_live.py -v --tb=short -m live

# ── Benchmarks ────────────────────────────────────────────────────────────────
bench:            ## Run full benchmark suite (10 / 100 / 1000 concurrency)
	$(PYTHON) benchmarks/bench_core.py --concurrency 10 100 1000

bench-fast:       ## Run quick benchmark (10 / 50 concurrency)
	$(PYTHON) benchmarks/bench_core.py --concurrency 10 50

bench-save:       ## Run benchmarks and save JSON results
	$(PYTHON) benchmarks/bench_core.py --concurrency 10 100 1000 --output benchmarks/results.json

# ── Dashboard ─────────────────────────────────────────────────────────────────
dashboard:        ## Launch Streamlit dashboard (needs: pip install streamlit pandas)
	streamlit run dashboard/app.py -- --server http://localhost:8000

# ── Docker ───────────────────────────────────────────────────────────────────
docker-build:     ## Build the MeshFlow Docker image
	docker build -t meshflow:latest .

docker-run:       ## Run MeshFlow in Docker (set MESHFLOW_API_KEYS and ANTHROPIC_API_KEY)
	docker run -p 8000:8000 \
		-e MESHFLOW_API_KEYS=$(MESHFLOW_API_KEYS) \
		-e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
		meshflow:latest

docker-push:      ## Push to Docker Hub (set DOCKER_REGISTRY)
	docker tag meshflow:latest $(DOCKER_REGISTRY)/meshflow:latest
	docker push $(DOCKER_REGISTRY)/meshflow:latest

# ── Kubernetes ────────────────────────────────────────────────────────────────
k8s-apply:        ## Apply Kubernetes manifests
	kubectl apply -f k8s/

# ── Help ──────────────────────────────────────────────────────────────────────
help:             ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
