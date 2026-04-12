# =============================================================================
# AKS Health Backend – Docker image
# =============================================================================
# Multi-stage build:
#   aks-mcp-dl  – downloads the official Microsoft AKS MCP binary
#   builder     – installs Python dependencies into an isolated prefix
#   runtime     – lean production image
#
# The container runs:
#   uvicorn api.main:app   (FastAPI + Azure AD SSO)
#
# When an agent health query arrives, the FastAPI handler calls RootAgent,
# which spawns the aks-mcp binary as a child process (stdio transport).
# The binary, Python agents, and FastAPI backend all run inside this single
# container — no sidecar needed.
#
# Build:
#   docker build -t <registry>/aks-health-backend:latest .
#
# Run locally (without Kubernetes):
#   docker run --env-file .env -p 8000:8000 <registry>/aks-health-backend:latest
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 – download the official aks-mcp binary
#   github.com/Azure/aks-mcp  (Linux AMD64)
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS aks-mcp-dl

ARG AKS_MCP_VERSION=latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$AKS_MCP_VERSION" = "latest" ]; then \
        DOWNLOAD_URL=$(curl -fsSL https://api.github.com/repos/Azure/aks-mcp/releases/latest \
            | jq -r '.assets[] | select(.name | test("linux.*amd64|amd64.*linux")) | .browser_download_url' \
            | head -1); \
    else \
        DOWNLOAD_URL="https://github.com/Azure/aks-mcp/releases/download/${AKS_MCP_VERSION}/aks-mcp-linux-amd64"; \
    fi \
    && echo "Downloading aks-mcp from: $DOWNLOAD_URL" \
    && curl -fsSL "$DOWNLOAD_URL" -o /usr/local/bin/aks-mcp \
    && chmod +x /usr/local/bin/aks-mcp

# ---------------------------------------------------------------------------
# Stage 2 – Python dependency installation
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# build-essential is needed for wheels with C extensions (cryptography, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install ".[api]"

# ---------------------------------------------------------------------------
# Stage 3 – runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# azure-cli and kubectl are required by the aks-mcp binary at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg lsb-release \
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && az aks install-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Security: run as a non-root user
RUN groupadd -r appgroup \
    && useradd -r -g appgroup -u 1000 -s /sbin/nologin -d /app appuser

WORKDIR /app

# Copy the official aks-mcp binary from stage 1
COPY --from=aks-mcp-dl /usr/local/bin/aks-mcp /usr/local/bin/aks-mcp

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source
# PYTHONPATH=/app ensures that /app/server, /app/agents, /app/api are all
# importable, and take precedence over any wheel-installed copies.
COPY --chown=appuser:appgroup server/ server/
COPY --chown=appuser:appgroup agents/ agents/
COPY --chown=appuser:appgroup api/    api/

USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    AKS_MCP_BINARY=/usr/local/bin/aks-mcp

EXPOSE 8000

# Kubernetes liveness probe calls /healthz
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" \
        || exit 1

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-config", "/dev/null"]
