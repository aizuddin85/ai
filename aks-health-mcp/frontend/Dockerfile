# =============================================================================
# AKS Health MCP Server – Frontend Docker image
# =============================================================================
# Multi-stage build:
#   builder  – compiles the React / TypeScript app with Vite
#   runtime  – Nginx (unprivileged) serving the static bundle
#
# Azure AD values are baked into the JS bundle at build time via Vite's
# VITE_* environment variables.  They are not sensitive (client IDs and
# tenant IDs are public), but they must match your Azure AD app registration.
#
# Build:
#   docker build \
#     --build-arg VITE_AZURE_AD_CLIENT_ID=<app-reg-client-id> \
#     --build-arg VITE_AZURE_AD_TENANT_ID=<tenant-id> \
#     --build-arg VITE_AZURE_AD_ALLOWED_GROUP=<group-object-id> \
#     -t <registry>/aks-health-frontend:latest \
#     frontend/
#
# Run locally:
#   docker run -p 8080:8080 <registry>/aks-health-frontend:latest
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 – build
# ---------------------------------------------------------------------------
FROM node:18-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

COPY . .

# Build-time Azure AD variables (required)
ARG VITE_AZURE_AD_CLIENT_ID
ARG VITE_AZURE_AD_TENANT_ID
ARG VITE_AZURE_AD_ALLOWED_GROUP
ARG VITE_APP_NAME="AKS Health Dashboard"
# In production, point to your public backend URL; blank uses Vite proxy (dev only)
ARG VITE_API_BASE_URL=""

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 – runtime (nginx-unprivileged runs as uid 101, port 8080)
# ---------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.27-alpine

# Replace the default server block with our SPA config
COPY --from=builder /app/dist         /usr/share/nginx/html
COPY ../deploy/nginx.conf             /etc/nginx/conf.d/default.conf

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1
