# =============================================================================
# AKS Health MCP Server – Backend Docker image
# =============================================================================
# Multi-stage build:
#   builder  – installs Python dependencies into an isolated prefix
#   runtime  – lean production image; source copied to /app
#
# The container runs:
#   uvicorn api.main:app   (FastAPI + Azure AD SSO)
#
# When an agent health query arrives, the FastAPI handler calls RootAgent,
# which spawns the MCP server as a child process (stdio transport) using
# sys.executable.  All three layers (API, agents, MCP server) run inside
# this single container — no sidecar needed.
#
# Build:
#   docker build -t <registry>/aks-health-backend:latest .
#
# Run locally (without Kubernetes):
#   docker run --env-file .env -p 8000:8000 <registry>/aks-health-backend:latest
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 – dependency installation
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# build-essential is needed for wheels that have C extensions (cryptography, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install ".[api]"

# ---------------------------------------------------------------------------
# Stage 2 – runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Security: run as a non-root user
RUN groupadd -r appgroup \
    && useradd -r -g appgroup -u 1000 -s /sbin/nologin -d /app appuser

WORKDIR /app

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
    PYTHONPATH=/app

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
