# AKS Health MCP Server

Production-grade **Model Context Protocol (MCP) server** for monitoring Azure Kubernetes Service (AKS) health from both the **Azure control plane** and the **live in-cluster Kubernetes API**.

Includes a **multi-agent framework** powered by **Azure AI Foundry** and a **React sysadmin portal** with Azure AD SSO enforced at the AD security-group level.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Sysadmin Browser                         │
│              React + MSAL (Azure AD SSO / PKCE)              │
└────────────────────────┬─────────────────────────────────────┘
                         │  Bearer token (AD group-gated)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Backend                            │
│  JWT validation · groups-claim check · SSE streaming         │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│               Root Agent  (Azure AI Foundry)                 │
│   tools: query_azure_health  ·  query_cluster_health         │
└───────────────┬──────────────────────────┬───────────────────┘
                │  (concurrent)            │
                ▼                          ▼
  ┌─────────────────────┐    ┌──────────────────────┐
  │   AzureHealthAgent  │    │  ClusterHealthAgent  │
  │   (aks_* tools)     │    │  (k8s_* tools)       │
  └──────────┬──────────┘    └──────────┬───────────┘
             │                          │
             └────────────┬─────────────┘
                          ▼
          ┌───────────────────────────────┐
          │    AKS Health MCP Server      │  ← Strict read-only
          │   ┌───────────────────────┐   │
          │   │  Azure AKS tools      │───┼──▶ Azure Resource Manager API
          │   │  Kubernetes tools     │───┼──▶ Kubernetes API
          │   └───────────────────────┘   │
          └───────────────────────────────┘
```

---

## MCP Tools (16 total — all read-only)

### Azure Control Plane — `aks_*`

| Tool | Description |
|------|-------------|
| `aks_list_clusters` | List AKS clusters in a subscription / resource group |
| `aks_get_cluster_detail` | Full cluster config, network profile, OIDC settings |
| `aks_get_node_pools` | Node pool VM sizes, autoscaling, provisioning state |
| `aks_get_upgrade_profile` | Available Kubernetes upgrades (GA and preview) |
| `aks_get_resource_health_events` | Azure Resource Health incidents and maintenance |
| `aks_get_metrics` | Azure Monitor metrics (CPU, memory, pod counts) |

### In-Cluster Kubernetes — `k8s_*`

| Tool | Description |
|------|-------------|
| `k8s_get_nodes` | Node readiness, capacity, roles, kubelet version |
| `k8s_get_pods` | Pod phase, container states, restart counts |
| `k8s_get_deployments` | Deployment replica health |
| `k8s_get_daemonsets` | DaemonSet scheduling status |
| `k8s_get_statefulsets` | StatefulSet replica health |
| `k8s_get_events` | Cluster events (Warning / Normal), sorted by recency |
| `k8s_get_namespaces` | Namespace list and phases |
| `k8s_get_pvc_status` | PVC binding status and storage class |
| `k8s_get_services` | Service types, ports, load-balancer addresses |
| `k8s_get_component_status` | Control-plane component health |

---

## Authentication

### MCP Server & Agents — Azure credential chain

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | Service Principal (`ClientSecretCredential`) | `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` set |
| 2 | Azure CLI (`AzureCliCredential`) | User has run `az login` |
| 3 | Managed Identity (`ManagedIdentityCredential`) | Running inside Azure (AKS pod) |

Required Azure RBAC roles for the identity used:
- `Reader` on the subscription or resource group
- `Monitoring Reader` on the subscription (for Azure Monitor metrics)

### Azure AI Foundry — agent LLM

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | API key (`AzureKeyCredential`) | `AZURE_FOUNDRY_API_KEY` is set |
| 2 | Azure AD credential chain (above) | Key not set |

### Kubernetes

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | In-cluster ServiceAccount | `KUBERNETES_SERVICE_HOST` set or `K8S_IN_CLUSTER=true` |
| 2 | Kubeconfig | `KUBECONFIG` env or `~/.kube/config` |

Required Kubernetes RBAC (`ClusterRole` — read-only):
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: aks-health-mcp-reader
rules:
- apiGroups: [""]
  resources: ["nodes", "pods", "events", "namespaces",
               "persistentvolumeclaims", "services", "componentstatuses"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "daemonsets", "statefulsets"]
  verbs: ["get", "list", "watch"]
```

### SSO Portal — Azure AD group enforcement

The React frontend uses **MSAL PKCE redirect flow**. After login the FastAPI backend:
1. Validates the JWT signature using the tenant's JWKS (cached 1 h).
2. Checks the `groups` claim for the required AD security group OID.
3. Falls back to **Microsoft Graph `transitiveMemberOf`** if the user is in >200 groups.

Every API request must carry a valid bearer token. Users not in the group receive HTTP 403.

**App registration checklist:**
- Platform: **Single-page application**, redirect URI `https://<domain>/auth/callback`
- Token configuration → Add groups claim → **Security groups**
- Expose an API → scope: `user_impersonation`
- API permissions: `openid`, `profile`, `email`; optionally `GroupMember.Read.All`

---

## Configuration

Copy `.env.example` → `.env` and fill in the values.

### Backend / MCP Server

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_TENANT_ID` | ✅ | Azure AD tenant GUID |
| `AZURE_SUBSCRIPTION_ID` | | Default subscription for Azure tools |
| `AZURE_CLIENT_ID` | | Service principal app ID |
| `AZURE_CLIENT_SECRET` | | Service principal secret |
| `AZURE_FOUNDRY_ENDPOINT` | ✅ | Foundry inference URL, e.g. `https://<project>.services.ai.azure.com/models` |
| `AZURE_FOUNDRY_MODEL` | | Model deployment name (default: `gpt-4o`) |
| `AZURE_FOUNDRY_API_KEY` | | Foundry API key (leave blank → Azure AD auth) |
| `AZURE_AD_APP_CLIENT_ID` | ✅ | Client ID of the frontend app registration |
| `AZURE_AD_ALLOWED_GROUP` | ✅ | OID of the AD security group allowed to access the UI |
| `FRONTEND_ORIGIN` | | CORS origin (default: `http://localhost:5173`) |
| `KUBECONFIG` | | Path to kubeconfig (default: `~/.kube/config`) |
| `K8S_CONTEXT` | | Kubeconfig context override |
| `K8S_IN_CLUSTER` | | `true` to force in-cluster auth |
| `MCP_TRANSPORT` | | `stdio` (default) or `sse` |
| `LOG_LEVEL` | | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |
| `LOG_FORMAT` | | `json` (default) or `console` |

### Frontend (`frontend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_AZURE_AD_CLIENT_ID` | ✅ | Same as `AZURE_AD_APP_CLIENT_ID` |
| `VITE_AZURE_AD_TENANT_ID` | ✅ | Azure AD tenant GUID |
| `VITE_AZURE_AD_ALLOWED_GROUP` | ✅ | Same as `AZURE_AD_ALLOWED_GROUP` |
| `VITE_API_BASE_URL` | | Backend URL (blank = Vite proxy in dev) |
| `VITE_APP_NAME` | | Header title (default: `AKS Health Dashboard`) |

---

## Quick Start

### 1 — Install dependencies

```bash
git clone <repo>
cd aks-health-mcp

# Backend (MCP server + agents + API)
pip install -e ".[dev,api]"

# Frontend
cd frontend && npm install && cd ..
```

### 2 — Configure

```bash
cp .env.example .env
# Edit .env — at minimum set:
#   AZURE_TENANT_ID, AZURE_FOUNDRY_ENDPOINT,
#   AZURE_AD_APP_CLIENT_ID, AZURE_AD_ALLOWED_GROUP

cp frontend/.env.example frontend/.env
# Edit frontend/.env with the same AD values
```

### 3 — Run tests

```bash
make test        # 55 tests
```

### 4 — Start the stack

```bash
# Terminal 1 – FastAPI backend
make run-api

# Terminal 2 – React frontend (http://localhost:5173)
make run-frontend
```

Browse to `http://localhost:5173` → Microsoft login → dashboard.

### 5 — Use the agent directly (CLI)

```bash
make run-agent QUERY="What is the overall health of my AKS clusters?"
```

Or in Python:
```python
import asyncio
from agents.root_agent import RootAgent

async def main():
    agent = RootAgent()
    report = await agent.run(
        "What is the overall health of my AKS environment? "
        "Are there any critical issues I should act on?"
    )
    print(report)

asyncio.run(main())
```

---

## Security

| Control | Implementation |
|---------|---------------|
| Read-only MCP tools | No create/update/delete/patch SDK call is ever made; enforced by `test_no_write_tools_registered` |
| Credential isolation | Credentials loaded from env only; tool arguments never accept secrets |
| Secret masking | `pydantic.SecretStr` + structlog `_scrub_sensitive` processor on all log output |
| Audit logging | Every tool call and HTTP request logged with tool name, UPN, OID (no secret values) |
| AD group enforcement | Backend validates `groups` JWT claim + MS Graph fallback; frontend alone is not trusted |
| CORS | Single configured origin; no wildcards |
| Token validation | RS256 + JWKS cache (1 h TTL); audience accepts `<clientId>` and `api://<clientId>` |
| Rate limiting | Tenacity exponential back-off on all Azure API calls |
| MCP SSE transport | Binds to `127.0.0.1` by default; use a TLS-terminating reverse proxy for production |

---

## Project Structure

```
aks-health-mcp/
├── server/                        # MCP server
│   ├── main.py                    # FastMCP entry point, 16 tool registrations
│   ├── config.py                  # Pydantic-settings (SecretStr masking)
│   ├── logging_config.py          # structlog structured logging
│   ├── auth/
│   │   └── credentials.py         # Azure + K8s credential chain factories
│   └── tools/
│       ├── azure_aks.py           # Azure RM read tools (aks_*)
│       └── cluster_k8s.py         # Kubernetes read tools (k8s_*)
│
├── agents/                        # Azure AI Foundry multi-agent framework
│   ├── base.py                    # Base class: MCP stdio client + conversation loop
│   ├── root_agent.py              # Root orchestrator (concurrent sub-agent dispatch)
│   └── task_agents/
│       ├── azure_health_agent.py  # Scoped to aks_* tools
│       └── cluster_health_agent.py# Scoped to k8s_* tools
│
├── api/                           # FastAPI backend (SSO portal bridge)
│   ├── main.py                    # App factory, CORS, request logging, probes
│   ├── config.py                  # ApiSettings (extends server Settings)
│   ├── auth/
│   │   └── azure_ad.py            # JWT validation, JWKS cache, group check
│   └── routes/
│       ├── auth.py                # GET /api/auth/me
│       └── agent.py               # POST /api/agent/query (SSE stream)
│
├── frontend/                      # React sysadmin portal
│   ├── src/
│   │   ├── authConfig.ts          # MSAL config (PKCE, SessionStorage)
│   │   ├── App.tsx                # Router + MsalProvider
│   │   ├── api/agentApi.ts        # Typed API client (fetch + SSE parser)
│   │   ├── hooks/
│   │   │   ├── useGroupAuth.ts    # Silent token + /auth/me group check
│   │   │   └── useAgentQuery.ts   # SSE lifecycle: idle→loading→done
│   │   ├── components/
│   │   │   ├── ProtectedRoute.tsx # Auth + group gate
│   │   │   ├── Dashboard.tsx      # Main layout + history sidebar
│   │   │   ├── HealthReport.tsx   # Markdown renderer + skeleton loading
│   │   │   ├── QueryInput.tsx     # Textarea + suggested-query chips
│   │   │   ├── LoginPage.tsx      # Microsoft sign-in card
│   │   │   ├── AccessDenied.tsx   # Shown for unauthorised users
│   │   │   └── Header.tsx         # User info + sign-out
│   │   └── types/index.ts         # Shared TypeScript types
│   ├── package.json
│   ├── vite.config.ts             # Dev proxy → localhost:8000
│   └── tailwind.config.js         # Azure blue palette + custom typography
│
├── tests/                         # 55 tests
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_mcp_server.py
│   ├── test_azure_tools.py
│   ├── test_cluster_tools.py
│   ├── test_agents.py
│   └── test_api_auth.py
│
├── pyproject.toml                 # deps: base + api + dev extras
├── Makefile
└── .env.example
```
