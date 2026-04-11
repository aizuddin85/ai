.PHONY: install dev-install test lint typecheck run-stdio run-sse clean

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	pip install -e .

dev-install:
	pip install -e ".[dev]"

# ── Quality gates ─────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --cov=server --cov=agents --cov-report=term-missing

lint:
	ruff check server/ agents/ tests/

typecheck:
	mypy server/ agents/

check: lint typecheck test

# ── Run MCP server ────────────────────────────────────────────────────────────
run-stdio:
	python -m server.main

run-sse:
	MCP_TRANSPORT=sse python -m server.main

# ── Run root agent ────────────────────────────────────────────────────────────
run-agent:
	python -m agents.root_agent $(QUERY)

# Example:
#   make run-agent QUERY="What is the health of my AKS clusters?"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
