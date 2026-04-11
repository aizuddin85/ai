# AKS Health MCP Server

Production-grade **Model Context Protocol (MCP) server** for monitoring Azure Kubernetes Service (AKS) health from both the **Azure control plane** and the **live in-cluster Kubernetes API**.

Includes a **multi-agent framework** powered by **Azure AI Foundry** and a **React sysadmin portal** with Azure AD SSO enforced at the AD security-group level.

---

## Table of Contents

1. [Architecture](#architecture)
2. [MCP Tools](#mcp-tools)
3. [Prerequisites](#prerequisites)
4. [Azure Setup](#azure-setup)
   - [Find Your Tenant ID and Subscription ID](#1-find-your-tenant-id-and-subscription-id)
   - [Create an App Registration](#2-create-an-app-registration)
   - [Configure the App Registration](#3-configure-the-app-registration)
   - [Create a Service Principal](#4-create-a-service-principal-optional--for-ci--robotic-access)
   - [Create or Find an AD Security Group](#5-create-or-find-an-ad-security-group)
   - [Set Up Azure AI Foundry](#6-set-up-azure-ai-foundry)
5. [Installation](#installation)
6. [Configuration](#configuration)
   - [Backend `.env`](#backend-env)
   - [Frontend `frontend/.env`](#frontend-frontendenv)
7. [Running the Stack](#running-the-stack)
8. [Running Tests](#running-tests)
9. [Authentication Reference](#authentication-reference)
10. [Kubernetes RBAC](#kubernetes-rbac)
11. [Security Controls](#security-controls)
12. [Troubleshooting](#troubleshooting)
13. [Project Structure](#project-structure)

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

## MCP Tools

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

## Prerequisites

Install the following before starting:

| Tool | Minimum version | Installation |
|------|----------------|--------------|
| Python | 3.11 | https://www.python.org/downloads/ |
| Node.js | 18 LTS | https://nodejs.org/en/download |
| Azure CLI | 2.60 | https://learn.microsoft.com/en-us/cli/azure/install-azure-cli |
| kubectl | 1.28 | https://kubernetes.io/docs/tasks/tools/ |
| git | any | https://git-scm.com/downloads |

Verify everything is installed:

```bash
python --version      # Python 3.11+
node --version        # v18+
az --version          # azure-cli 2.60+
kubectl version --client
```

You also need:
- An **Azure subscription** with at least one AKS cluster
- An **Azure Active Directory tenant** (the same tenant that owns the subscription)
- An **Azure AI Foundry** project (for the agent LLM — see [Set Up Azure AI Foundry](#6-set-up-azure-ai-foundry))

---

## Azure Setup

> **What is a GUID / UUID?**  
> Throughout this guide you will copy IDs that look like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.  
> These are called GUIDs (Globally Unique Identifiers). They always follow that 8-4-4-4-12 hex format.

### 1. Find Your Tenant ID and Subscription ID

**Tenant ID**

1. Go to the [Azure Portal](https://portal.azure.com).
2. In the top-left search bar type **Azure Active Directory** and select it.
3. On the **Overview** page, copy the **Tenant ID** (a GUID).

**Subscription ID**

1. In the top-left search bar type **Subscriptions** and select it.
2. Click your subscription.
3. On the **Overview** page, copy the **Subscription ID** (a GUID).

---

### 2. Create an App Registration

The app registration is the Azure AD identity that your React frontend uses to log users in.

1. In the [Azure Portal](https://portal.azure.com), search for **App registrations** and select it.
2. Click **+ New registration**.
3. Fill in:
   - **Name**: `aks-health-portal` (or any name you like)
   - **Supported account types**: *Accounts in this organizational directory only (Single tenant)*
   - **Redirect URI**: Select **Single-page application (SPA)**, then enter:
     - `http://localhost:5173/auth/callback` (for local development)
4. Click **Register**.
5. On the **Overview** page, copy the **Application (client) ID** — this is your `AZURE_AD_APP_CLIENT_ID` and `VITE_AZURE_AD_CLIENT_ID`.

---

### 3. Configure the App Registration

After creating the registration, you need three more steps.

#### 3a. Expose an API scope

1. From your app registration, go to **Expose an API**.
2. Click **+ Add a scope**.
3. If prompted to set an Application ID URI, accept the default (`api://<your-client-id>`) and click **Save and continue**.
4. Fill in:
   - **Scope name**: `user_impersonation`
   - **Who can consent**: *Admins and users*
   - **Admin consent display name**: `Access AKS Health Portal`
   - **Admin consent description**: `Allows the app to access the AKS Health Portal on behalf of the signed-in user`
5. Click **Add scope**.

#### 3b. Add groups claim to tokens

This makes Azure AD include the user's group memberships in the JWT token.

1. From your app registration, go to **Token configuration**.
2. Click **+ Add groups claim**.
3. Select **Security groups**.
4. Under **ID**, **Access**, and **SAML** columns, make sure **Group ID** is checked.
5. Click **Add**.

#### 3c. Grant API permissions

1. From your app registration, go to **API permissions**.
2. Click **+ Add a permission**.
3. Select **Microsoft Graph** → **Delegated permissions**.
4. Add: `openid`, `profile`, `email`.
5. Optionally add `GroupMember.Read.All` (required only if users are members of more than 200 groups).
6. Click **Add permissions**.
7. Click **Grant admin consent for [your tenant]** and confirm.

---

### 4. Create a Service Principal (optional — for CI / robotic access)

Skip this step if you will only use `az login` (developer/local access). A service principal is needed for automated pipelines or production deployments where no human login is possible.

```bash
# Log in first
az login

# Create the service principal and assign Reader role
az ad sp create-for-rbac \
  --name "aks-health-mcp-sp" \
  --role Reader \
  --scopes /subscriptions/<your-subscription-id>

# The output will show:
# {
#   "appId":       "<AZURE_CLIENT_ID>",
#   "password":    "<AZURE_CLIENT_SECRET>",
#   "tenant":      "<AZURE_TENANT_ID>"
# }
```

Save the `appId` and `password` — you will need them for `.env`.

Also assign the **Monitoring Reader** role so the SP can read Azure Monitor metrics:

```bash
az role assignment create \
  --assignee <appId> \
  --role "Monitoring Reader" \
  --scope /subscriptions/<your-subscription-id>
```

---

### 5. Create or Find an AD Security Group

Only members of this group will be allowed to use the dashboard.

**Create a new group:**

1. In the Azure Portal, search for **Groups** and select it.
2. Click **+ New group**.
3. Fill in:
   - **Group type**: Security
   - **Group name**: `aks-sysadmins` (or any name)
4. Under **Members**, add the users who should have access.
5. Click **Create**.
6. Open the group you just created, go to **Overview**, and copy the **Object ID** (a GUID).  
   This is your `AZURE_AD_ALLOWED_GROUP` and `VITE_AZURE_AD_ALLOWED_GROUP`.

**Use an existing group:**

1. In the Azure Portal, search for **Groups**, find your group.
2. Click the group → **Overview** → copy the **Object ID**.

---

### 6. Set Up Azure AI Foundry

Azure AI Foundry hosts the LLM model that powers the health agents.

1. Go to [Azure AI Foundry](https://ai.azure.com) and sign in.
2. Click **+ New project** (or use an existing one).
3. Give it a name and select your Azure subscription and region.
4. Once the project is created, go to the project and click **Deployments** on the left.
5. Click **+ Deploy model** → choose **gpt-4o** (or any chat model).
6. Note the **Deployment name** — this is your `AZURE_FOUNDRY_MODEL`.
7. To get the endpoint URL:
   - Go to the project's **Overview** page.
   - Look for **Azure AI Foundry endpoint** or **Target URI**.
   - It looks like: `https://<project-name>.services.ai.azure.com/models`
   - This is your `AZURE_FOUNDRY_ENDPOINT`.
8. To get an API key (optional — you can use Azure AD instead):
   - Go to **Settings** → **Keys and Endpoint**.
   - Copy **Key 1** — this is your `AZURE_FOUNDRY_API_KEY`.
   - If you leave this blank, the system uses Azure AD credentials (recommended).

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd aks-health-mcp

# 2. Install Python dependencies (backend + agents + API)
pip install -e ".[dev,api]"

# 3. Install frontend dependencies
cd frontend
npm install
cd ..
```

> **Tip:** It is good practice to use a Python virtual environment:
> ```bash
> python -m venv .venv
> source .venv/bin/activate   # macOS/Linux
> .venv\Scripts\activate      # Windows
> pip install -e ".[dev,api]"
> ```

---

## Configuration

### Backend `.env`

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` in a text editor. Below is a description of every field:

```bash
# ── Azure Authentication ──────────────────────────────────────────────────────
# Your Azure AD Tenant ID (GUID from Step 1)
AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000

# One or more Azure subscription IDs to query (comma-separated).
# Single subscription:
AZURE_SUBSCRIPTION_IDS=11111111-1111-1111-1111-111111111111
# Multiple subscriptions (aks_list_clusters and health events iterate all of them):
# AZURE_SUBSCRIPTION_IDS=11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222

# Service principal credentials (from Step 4).
# Leave blank if using `az login` for local development.
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=

# ── Kubernetes ────────────────────────────────────────────────────────────────
# Leave blank to use ~/.kube/config (the default after `az aks get-credentials`)
KUBECONFIG=
# Leave blank to use the current context in your kubeconfig
K8S_CONTEXT=
# Set to true only if deploying this server as a pod inside an AKS cluster
K8S_IN_CLUSTER=false

# ── MCP Server ────────────────────────────────────────────────────────────────
# stdio = agents launch the server as a child process (default, recommended)
# sse   = server listens on HTTP for direct MCP connections
MCP_TRANSPORT=stdio
MCP_HOST=127.0.0.1
MCP_PORT=8090

# ── Azure AI Foundry ──────────────────────────────────────────────────────────
# Inference endpoint URL from Step 6
AZURE_FOUNDRY_ENDPOINT=https://<project-name>.services.ai.azure.com/models
# The model deployment name from Step 6
AZURE_FOUNDRY_MODEL=gpt-4o
# API key from Step 6. Leave blank to use Azure AD credential chain instead.
AZURE_FOUNDRY_API_KEY=

# ── Frontend / SSO ────────────────────────────────────────────────────────────
# Application (client) ID of your app registration (from Step 2)
AZURE_AD_APP_CLIENT_ID=22222222-2222-2222-2222-222222222222
# Object ID of the AD security group (from Step 5)
AZURE_AD_ALLOWED_GROUP=33333333-3333-3333-3333-333333333333
# URL of the React frontend (for CORS). Default is fine for local dev.
FRONTEND_ORIGIN=http://localhost:5173

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO   # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=json  # json | console
```

> **Security note:** Never commit `.env` to git. It is already in `.gitignore`.

---

### Frontend `frontend/.env`

```bash
cp frontend/.env.example frontend/.env
```

Open `frontend/.env`:

```bash
# Application (client) ID — same value as AZURE_AD_APP_CLIENT_ID above
VITE_AZURE_AD_CLIENT_ID=22222222-2222-2222-2222-222222222222

# Tenant ID — same value as AZURE_TENANT_ID above
VITE_AZURE_AD_TENANT_ID=00000000-0000-0000-0000-000000000000

# Allowed group Object ID — same value as AZURE_AD_ALLOWED_GROUP above
VITE_AZURE_AD_ALLOWED_GROUP=33333333-3333-3333-3333-333333333333

# Leave blank for local development (Vite proxies /api → localhost:8000)
VITE_API_BASE_URL=

# Title shown in the browser tab and header
VITE_APP_NAME=AKS Health Dashboard
```

---

## Running the Stack

### Step 1 — Connect to your AKS cluster

```bash
# Replace <resource-group> and <cluster-name> with your values
az aks get-credentials \
  --resource-group <resource-group> \
  --name <cluster-name>

# Verify the connection
kubectl get nodes
```

### Step 2 — Start the FastAPI backend

Open a terminal and run:

```bash
make run-api
```

You should see output like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Verify it is healthy:

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

### Step 3 — Start the React frontend

Open a **second** terminal and run:

```bash
make run-frontend
```

You should see:

```
  VITE v6.x  ready in xxx ms
  ➜  Local:   http://localhost:5173/
```

### Step 4 — Open the dashboard

Browse to **http://localhost:5173** in your browser.

You will be redirected to the Microsoft login page. Sign in with an account that is a member of the AD security group you configured. After login you will land on the AKS Health Dashboard.

### Step 5 — Run a health query

Type a question into the input box, for example:

> *What is the overall health of my AKS clusters? Are there any critical issues?*

The agent will connect to the MCP server, call the relevant Azure and Kubernetes tools, and stream a formatted health report back to your browser.

---

### CLI agent (no frontend)

You can also run the agent directly from the terminal without the frontend:

```bash
make run-agent QUERY="What is the overall health of my AKS clusters?"
```

Or from Python:

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

## Running Tests

```bash
make test
```

Expected output: **55 tests pass**.

To also see code coverage:

```bash
make test-cov
```

---

## Authentication Reference

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

### SSO Portal — Azure AD group enforcement

The React frontend uses **MSAL PKCE redirect flow**. After login the FastAPI backend:

1. Validates the JWT signature using the tenant's JWKS (cached 1 h).
2. Checks the `groups` claim for the required AD security group OID.
3. Falls back to **Microsoft Graph `transitiveMemberOf`** if the user is in >200 groups.

Every API request must carry a valid bearer token. Users not in the group receive HTTP 403.

---

## Kubernetes RBAC

If running the MCP server inside an AKS pod, create this read-only ClusterRole:

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

Apply it:

```bash
kubectl apply -f clusterrole.yaml

# Bind to the pod's ServiceAccount
kubectl create clusterrolebinding aks-health-mcp-reader \
  --clusterrole=aks-health-mcp-reader \
  --serviceaccount=<namespace>:<serviceaccount-name>
```

---

## Security Controls

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

## Troubleshooting

### `azure_tenant_id must be a valid GUID`

You copied `.env.example` but left `AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000` unchanged, or entered a non-GUID value. Replace it with your actual Tenant ID from the Azure Portal (see [Step 1](#1-find-your-tenant-id-and-subscription-id)).

### `az login` required / `ChainedTokenCredential` failed

You have no credentials configured. Either:
- Run `az login` for local development, **or**
- Set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, and `AZURE_TENANT_ID` for service principal auth.

### `kubectl: command not found` or `Unable to connect to the server`

Run `az aks get-credentials --resource-group <rg> --name <cluster>` to download the kubeconfig. Verify with `kubectl get nodes`.

### Frontend shows "Access Denied"

The signed-in Azure AD account is not a member of the group specified in `AZURE_AD_ALLOWED_GROUP`. Add the user to the group in the Azure Portal (see [Step 5](#5-create-or-find-an-ad-security-group)).

### `Missing VITE_AZURE_AD_TENANT_ID or VITE_AZURE_AD_CLIENT_ID`

You forgot to create `frontend/.env`. Run:

```bash
cp frontend/.env.example frontend/.env
# then edit frontend/.env with your values
```

### Agent returns "Tool execution failed"

Check that:
1. The MCP server can start on its own: `python -m server.main` (it will block on stdin — press Ctrl+C to exit; if it starts without errors, the server is working).
2. The Azure credential has the `Reader` and `Monitoring Reader` roles.
3. The kubeconfig is pointing to the right cluster.

### `403 Forbidden` on `/api/auth/me` in Postman / curl

The request is missing an `Authorization: Bearer <token>` header. Every API call requires a valid Azure AD token.

### Backend starts but CORS errors appear in the browser console

`FRONTEND_ORIGIN` in `.env` does not match the URL your browser is using. For local development it must be exactly `http://localhost:5173` (no trailing slash).

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
├── .env.example                   # Copy to .env and fill in values
└── frontend/.env.example          # Copy to frontend/.env and fill in values
```
