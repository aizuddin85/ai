.PHONY: install dev-install test lint typecheck run-agent install-aks-mcp clean

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	pip install -e .

dev-install:
	pip install -e ".[dev,api]"

api-install:
	pip install -e ".[api]"

# ── Install the official Microsoft AKS MCP server binary ─────────────────────
# Downloads the latest Linux AMD64 release of github.com/Azure/aks-mcp to
# ./bin/aks-mcp.  Override AKS_MCP_VERSION to pin a specific release tag.
AKS_MCP_VERSION ?= latest
AKS_MCP_OS      ?= linux
AKS_MCP_ARCH    ?= amd64
BIN_DIR         := $(CURDIR)/bin

install-aks-mcp:
	@mkdir -p $(BIN_DIR)
	@echo "Downloading aks-mcp ($(AKS_MCP_VERSION)) for $(AKS_MCP_OS)/$(AKS_MCP_ARCH)..."
	@if [ "$(AKS_MCP_VERSION)" = "latest" ]; then \
		DOWNLOAD_URL=$$(curl -fsSL https://api.github.com/repos/Azure/aks-mcp/releases/latest \
			| grep '"browser_download_url"' \
			| grep '$(AKS_MCP_OS)' \
			| grep '$(AKS_MCP_ARCH)' \
			| head -1 \
			| sed 's/.*"browser_download_url": "\(.*\)".*/\1/'); \
	else \
		DOWNLOAD_URL="https://github.com/Azure/aks-mcp/releases/download/$(AKS_MCP_VERSION)/aks-mcp-$(AKS_MCP_OS)-$(AKS_MCP_ARCH)"; \
	fi; \
	curl -fsSL "$$DOWNLOAD_URL" -o $(BIN_DIR)/aks-mcp && chmod +x $(BIN_DIR)/aks-mcp
	@echo "aks-mcp installed to $(BIN_DIR)/aks-mcp"
	@echo "Set AKS_MCP_BINARY=$(BIN_DIR)/aks-mcp in your .env (or ensure it is on PATH)."

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

# ── Run root agent ────────────────────────────────────────────────────────────
run-agent:
	python -m agents.root_agent $(QUERY)

# Example:
#   make run-agent QUERY="What is the health of my AKS clusters?"

# ── Run FastAPI backend ───────────────────────────────────────────────────────
run-api:
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# ── Run frontend dev server ───────────────────────────────────────────────────
run-frontend:
	cd frontend && npm run dev

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
