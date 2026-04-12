# AKS Health Dashboard

AI-powered AKS health monitoring dashboard that combines the **official Microsoft AKS MCP server** ([Azure/aks-mcp](https://github.com/Azure/aks-mcp)) with a **multi-agent framework** powered by **Azure AI Foundry** and a **React sysadmin portal** with Azure AD SSO.

The official `aks-mcp` binary is the sole MCP server: it provides rich AKS + Kubernetes tooling (Azure CLI, kubectl, Cilium, Helm, eBPF observability) maintained by Microsoft. This project adds the AI orchestration layer on top: firewall, hallucination review, concurrent sub-agents, and an SSE-streaming portal.

Access to Azure resources is governed by each user's **Azure RBAC role assignments** — no hard-coded group configuration required.

---

## Table of Contents

1. [Architecture](#architecture)
2. [MCP Tools](#mcp-tools)
3. [Session Isolation](#session-isolation)
4. [Prerequisites](#prerequisites)
5. [Azure Setup](#azure-setup)
   - [Find Your Tenant ID and Subscription ID](#1-find-your-tenant-id-and-subscription-id)
   - [Create an App Registration](#2-create-an-app-registration)
   - [Configure the App Registration](#3-configure-the-app-registration)
   - [Assign Azure RBAC Roles to Users](#4-assign-azure-rbac-roles-to-users)
   - [Set Up Azure AI Foundry](#5-set-up-azure-ai-foundry)
6. [Installation](#installation)
7. [Configuration](#configuration)
   - [Backend `.env`](#backend-env)
   - [Frontend `frontend/.env`](#frontend-frontendenv)
8. [Running the Stack](#running-the-stack)
9. [Kubernetes Deployment (Docker + Helm)](#kubernetes-deployment-docker--helm)
   - [Prerequisites for Kubernetes](#prerequisites-for-kubernetes)
   - [Build and Push Docker Images](#build-and-push-docker-images)
   - [Deploy with Helm](#deploy-with-helm)
   - [Azure Workload Identity (Recommended)](#azure-workload-identity-recommended)
   - [Service Principal Authentication](#service-principal-authentication)
   - [Helm Values Reference](#helm-values-reference)
10. [Running Tests](#running-tests)
11. [Authentication Reference](#authentication-reference)
12. [Kubernetes RBAC](#kubernetes-rbac)
13. [Security Controls](#security-controls)
14. [Troubleshooting](#troubleshooting)
15. [Project Structure](#project-structure)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Sysadmin Browser                         │
│              React + MSAL (Azure AD SSO / PKCE)              │
└────────────────────────┬─────────────────────────────────────┘
                         │  Bearer token (Azure AD JWT)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Backend  (Python)                  │
│  JWT validation · OBO token exchange · SSE streaming         │
│  Per-request asyncio task · structlog contextvars isolated   │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  AI Firewall  (fast regex + LLM classifier)         │     │
│  │  Blocks: prompt injection · credential extraction   │     │
│  │          destructive ops · off-topic queries         │     │
│  └─────────────────────────────────────────────────────┘     │
└────────────────────────┬─────────────────────────────────────┘
                         │  per-request RootAgent instance
                         ▼
┌──────────────────────────────────────────────────────────────┐
│               Root Agent  (Azure AI Foundry)                 │
│   tools: query_azure_health  ·  query_cluster_health         │
└───────────────┬──────────────────────────┬───────────────────┘
                │  (concurrent asyncio)    │
                ▼                          ▼
  ┌─────────────────────┐    ┌──────────────────────┐
  │   AzureHealthAgent  │    │  ClusterHealthAgent  │
  │  az_* / aks_* tools │    │  call_kubectl / etc  │
  └──────────┬──────────┘    └──────────┬───────────┘
             │                          │
             └──────────┬───────────────┘
                        │  stdio (fresh child process per agent run)
                        ▼
          ┌────────────────────────────────────┐
          │  aks-mcp  (official Microsoft Go   │  ← --access-level readonly
          │  binary · github.com/Azure/aks-mcp)│
          │                                    │
          │  az_aks_operations ─────────────── │──▶ Azure CLI  (az)
          │  aks_monitoring ────────────────── │──▶ Azure Monitor / ARM
          │  aks_network_resources ──────────  │──▶ Azure Networking API
          │  aks_detector / advisor ─────────  │──▶ Azure Diagnostics
          │  call_kubectl ──────────────────── │──▶ Kubernetes API
          │  collect_aks_node_logs ──────────  │──▶ Node SSH / AKS API
          │  inspektor_gadget_observability ── │──▶ eBPF (in-cluster)
          │  call_helm / call_cilium ────────  │──▶ cluster tooling
          └────────────────────────────────────┘
                        │
                        ▼
          ┌──────────────────────────────────────┐
          │  Hallucination Reviewer              │
          │  LLM cross-checks answer vs raw data │
          │  corrects fabricated facts; appends  │
          │  transparency note when corrected    │
          └──────────────────────────────────────┘
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| Official `aks-mcp` binary as MCP server | Maintained by Microsoft; broader tool coverage; no custom Azure/k8s SDK code to maintain |
| `--access-level readonly` enforced | Hard guard at the binary level — no write tools exposed regardless of agent instructions |
| Fresh child process per agent invocation | Complete process isolation between requests; no shared in-memory state between users |
| Semaphore limits 5 concurrent agent calls | Caps resource consumption (each invocation spawns 2 aks-mcp child processes) |
| structlog `contextvars` (not thread-locals) | Per-asyncio-task log context; bound at request start, unbound in `finally`; zero cross-session leakage |
| Foundry secrets excluded from child env | Principle of least privilege — aks-mcp only receives Azure SDK credential vars it actually needs |

---

## MCP Tools

Tools are provided by the official **[Azure/aks-mcp](https://github.com/Azure/aks-mcp)** binary.  
The backend runs the binary with `--access-level readonly`, which restricts it to read and diagnostic operations only.

### Azure control-plane tools (exposed to `AzureHealthAgent`)

| Tool | Description |
|------|-------------|
| `az_aks_operations` | AKS cluster and node pool queries via Azure CLI |
| `aks_network_resources` | VNets, subnets, NSGs, route tables, load balancers |
| `aks_monitoring` | Azure Monitor metrics, Application Insights, diagnostic logs |
| `aks_detector` | Azure Diagnostics detector reports |
| `aks_advisor_recommendation` | Azure Advisor cost/reliability recommendations |
| `az_fleet` | Azure Fleet multi-cluster management |
| `az_compute_operations` | VM and VMSS management queries |
| `get_aks_vmss_info` | VMSS configuration for node pools |
| `call_az` | Raw Azure CLI fallback for ad-hoc queries |

### In-cluster / workload tools (exposed to `ClusterHealthAgent`)

| Tool | Description |
|------|-------------|
| `call_kubectl` | Flexible kubectl queries (nodes, pods, events, deployments…) |
| `collect_aks_node_logs` | Node system logs: kubelet, containerd, kernel, syslog |
| `inspektor_gadget_observability` | eBPF-based DNS, TCP, file-ops, process tracing |
| `call_helm` | Helm release and chart queries |
| `call_cilium` | Cilium networking CLI |
| `call_hubble` | Hubble network observability (Cilium) |

> **Tool prefix routing:** `AzureHealthAgent` receives tools starting with `az_`, `aks_`, `get_aks_`, or `call_az`.  
> `ClusterHealthAgent` receives tools starting with `call_kubectl`, `call_helm`, `call_cilium`, `call_hubble`, `collect_`, or `inspektor_`.  
> Tools outside these prefixes (e.g. write operations enabled at higher access levels) are never exposed to the models.

---

## Session Isolation

Every layer of the stack is designed so that one user's request cannot read, influence, or corrupt another user's request.

### Request-level process isolation

```
Request A (user Alice)           Request B (user Bob)
─────────────────────────        ─────────────────────────
RootAgent() ← fresh instance     RootAgent() ← fresh instance
  AzureHealthAgent()               AzureHealthAgent()
    aks-mcp child PID 1234           aks-mcp child PID 5678  ← separate OS process
  ClusterHealthAgent()             ClusterHealthAgent()
    aks-mcp child PID 1235           aks-mcp child PID 5679  ← separate OS process
```

Each HTTP request creates a **new `RootAgent` instance** (and therefore new `AzureHealthAgent` / `ClusterHealthAgent` instances), so `tool_results` and conversation history never cross between requests.  
Each agent invocation spawns a **fresh `aks-mcp` child process** via stdio transport. When the request ends, the process is terminated. No state persists between requests at the MCP layer.

### Logging context isolation

`structlog` uses Python's `contextvars` module for per-request log context. `contextvars.ContextVar` values are isolated to the asyncio task that set them — a value bound in one request's task is invisible to other tasks.

```python
# On entry to each agent SSE stream:
structlog.contextvars.bind_contextvars(query_id=..., user_oid=...)
# In the finally block, always cleaned up:
structlog.contextvars.unbind_contextvars("query_id", "user_oid")
```

The HTTP middleware binds `request_id` before calling into the handler. The agent layer **does not call `clear_contextvars()`** (which would drop `request_id`) — it only adds and later removes its own keys.

### Child-process environment filtering

Before spawning an `aks-mcp` subprocess, the agent layer **strips application-layer secrets** that the binary does not need:

| Excluded variable | Reason |
|-------------------|--------|
| `AZURE_FOUNDRY_API_KEY` | Foundry auth secret — no use to aks-mcp |
| `AZURE_FOUNDRY_ENDPOINT` | Foundry URL — no use to aks-mcp |
| `AZURE_FOUNDRY_MODEL` | Foundry model name — no use to aks-mcp |
| `AZURE_ARM_TOKEN` | Deprecated OBO field — not supported by aks-mcp |

All standard Azure SDK credential vars (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_FEDERATED_TOKEN_FILE`, etc.) are passed through so the binary can authenticate.

### Concurrency cap

```python
_AGENT_SEMAPHORE = asyncio.Semaphore(5)
```

A module-level semaphore limits the process to **5 concurrent agent invocations**. Each invocation spawns up to 2 aks-mcp processes, so the cap prevents runaway resource consumption from concurrent requests.

### Error message sanitisation

Internal exception messages (stack traces, file paths, internal identifiers) are **not returned to the client**. The SSE stream sends a generic `"An unexpected error occurred"` message; full details are emitted to server logs only.

### JWKS cache isolation

Azure AD public key material is cached per-tenant, keyed by `tenant_id`. The cache is bounded to 10 tenants with a 1-hour TTL. Different tenant entries cannot interfere with each other.

### What is NOT isolated (known limitations)

| Item | Details |
|------|---------|
| **Azure credential** | All requests share the server's identity (Service Principal or Workload Identity). OBO token flow performs the token exchange in the API layer but the `aks-mcp` binary uses its own Azure SDK credential chain, not a per-request injected token. In practice this means Azure RBAC is enforced at the subscription/resource-group level on the server's managed identity, not per-user. |
| **Foundry LLM calls** | All firewall, reviewer, and agent LLM calls use a single shared `ChatCompletionsClient` constructed at agent instantiation time. Token quotas are pooled across requests. |

---

## Prerequisites

Install the following before starting:

| Tool | Minimum version | Installation |
|------|----------------|--------------|
| Python | 3.11 | https://www.python.org/downloads/ |
| Node.js | 18 LTS | https://nodejs.org/en/download |
| Azure CLI | 2.60 | https://learn.microsoft.com/en-us/cli/azure/install-azure-cli |
| kubectl | 1.28 | https://kubernetes.io/docs/tasks/tools/ |
| **aks-mcp** | latest | `make install-aks-mcp` (see [Installation](#installation)) |
| git | any | https://git-scm.com/downloads |

Verify everything is installed:

```bash
python --version      # Python 3.11+
node --version        # v18+
az --version          # azure-cli 2.60+
kubectl version --client
aks-mcp --version     # after running make install-aks-mcp
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

#### 3b. Add a client secret (for On-Behalf-Of token exchange)

The backend needs a client secret to perform the **On-Behalf-Of (OBO)** flow — exchanging the user's login token for an Azure Resource Manager token so API calls run as the signed-in user.

1. From your app registration, go to **Certificates & secrets**.
2. Click **+ New client secret**.
3. Enter a description (e.g. `aks-health-backend`) and choose an expiry.
4. Click **Add**.
5. Copy the **Value** immediately — it will not be shown again.  
   This is your `AZURE_CLIENT_SECRET` in `.env`.

#### 3c. Grant API permissions

1. From your app registration, go to **API permissions**.
2. Click **+ Add a permission**.
3. Select **Microsoft Graph** → **Delegated permissions** → add: `openid`, `profile`, `email`.
4. Click **+ Add a permission** again.
5. Select **Azure Service Management** → **Delegated permissions** → add: `user_impersonation`.  
   *(This allows the backend to call Azure Resource Manager on behalf of the user.)*
6. Click **Add permissions**.
7. Click **Grant admin consent for [your tenant]** and confirm.

---

### 4. Assign Azure RBAC Roles to Users

Access to Azure resources is controlled by each user's Azure RBAC role assignments. Users (or their AD groups) must have at least the following roles on the subscriptions they want to query:

| Role | Purpose |
|------|---------|
| `Reader` | List AKS clusters, node pools, resource health, upgrade profiles |
| `Monitoring Reader` | Read Azure Monitor metrics (CPU, memory, pod counts) |

**Assign a role:**

```bash
# Assign Reader to a specific user
az role assignment create \
  --assignee <user-email-or-object-id> \
  --role Reader \
  --scope /subscriptions/<subscription-id>

# Assign Monitoring Reader to the same user
az role assignment create \
  --assignee <user-email-or-object-id> \
  --role "Monitoring Reader" \
  --scope /subscriptions/<subscription-id>
```

You can also assign roles to an **AD security group** — all group members inherit the roles automatically. This is the recommended approach for team access control.

> **How access works:** When a user logs in and submits a query, the backend exchanges their Azure AD token for an Azure Resource Manager token (OBO flow). Azure evaluates the user's RBAC roles and returns only the resources they can access. Users without any role on a subscription will see empty results or access-denied errors from Azure — not a login failure.

---

### 5. Set Up Azure AI Foundry

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

# 2. Download the official Microsoft AKS MCP binary (Linux AMD64)
make install-aks-mcp
# Binary is saved to ./bin/aks-mcp and chmod +x'd automatically.
# Override defaults if needed:
#   make install-aks-mcp AKS_MCP_OS=darwin AKS_MCP_ARCH=arm64

# 3. Install Python dependencies (backend + agents + API)
pip install -e ".[dev,api]"

# 4. Install frontend dependencies
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
# ── Azure Tenant ──────────────────────────────────────────────────────────────
# Your Azure AD Tenant ID (GUID from Step 1)
AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000

# One or more Azure subscription IDs (comma-separated).
AZURE_SUBSCRIPTION_IDS=11111111-1111-1111-1111-111111111111
# Multiple: AZURE_SUBSCRIPTION_IDS=11111111-...,22222222-...

# ── App Registration ──────────────────────────────────────────────────────────
# Application (client) ID of your app registration (from Step 2)
AZURE_AD_APP_CLIENT_ID=22222222-2222-2222-2222-222222222222

# Client secret of the SAME app registration (from Step 3b).
# Used for the On-Behalf-Of (OBO) token exchange so the API can confirm
# the caller's identity. Leave blank to fall back to `az login` (local dev)
# or Workload Identity (AKS pod).
AZURE_CLIENT_SECRET=

# ── Official AKS MCP server (github.com/Azure/aks-mcp) ───────────────────────
# Path to the aks-mcp binary. Run `make install-aks-mcp` to download it.
AKS_MCP_BINARY=./bin/aks-mcp
# Access level passed to the binary. Keep as readonly unless you know what
# you are doing — write access enables cluster mutations.
AKS_MCP_ACCESS_LEVEL=readonly

# ── Azure AI Foundry ──────────────────────────────────────────────────────────
# Inference endpoint URL from Step 5
AZURE_FOUNDRY_ENDPOINT=https://<project-name>.services.ai.azure.com/models
# The model deployment name from Step 5
AZURE_FOUNDRY_MODEL=gpt-4o
# API key from Step 5. Leave blank to use Azure AD credential chain instead.
AZURE_FOUNDRY_API_KEY=

# ── Frontend / CORS ───────────────────────────────────────────────────────────
# URL of the React frontend (for CORS). Default is fine for local dev.
FRONTEND_ORIGIN=http://localhost:5173

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO   # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=json  # json | console
```

> **Security note:** Never commit `.env` to git. It is already in `.gitignore`.

> **Kubernetes auth:** The `aks-mcp` binary discovers kubeconfig automatically — it reads `KUBECONFIG` env var or `~/.kube/config`, exactly like `kubectl`. You do not need to configure this separately.

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

You will be redirected to the Microsoft login page. Sign in with any Azure AD account in your tenant. After login you will land on the AKS Health Dashboard. The resources shown are determined by your Azure RBAC role assignments.

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

## Kubernetes Deployment (Docker + Helm)

This section covers building Docker images, pushing them to a registry, and deploying the full stack to AKS using the included Helm chart.

---

### Prerequisites for Kubernetes

In addition to the base prerequisites you will also need:

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Docker | 24 | Build and push images |
| Helm | 3.12 | Deploy the Helm chart |
| A container registry | — | Azure Container Registry (ACR) recommended |

Install Helm:

```bash
# macOS
brew install helm

# Linux
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Verify
helm version
```

---

### Build and Push Docker Images

#### 1. Log in to Azure Container Registry

```bash
# Replace <registry-name> with your ACR name (e.g. mycompanyacr)
az acr login --name <registry-name>
```

#### 2. Build and push the backend image

The backend Dockerfile lives at `aks-health-mcp/Dockerfile`.

```bash
cd aks-health-mcp

docker build \
  -t <registry-name>.azurecr.io/aks-health-backend:1.0.0 \
  -f Dockerfile \
  .

docker push <registry-name>.azurecr.io/aks-health-backend:1.0.0
```

#### 3. Build and push the frontend image

The frontend requires `VITE_*` values to be baked in at build time via Docker `--build-arg`:

```bash
docker build \
  -t <registry-name>.azurecr.io/aks-health-frontend:1.0.0 \
  -f frontend/Dockerfile \
  --build-arg VITE_AZURE_AD_CLIENT_ID=<your-client-id> \
  --build-arg VITE_AZURE_AD_TENANT_ID=<your-tenant-id> \
  --build-arg VITE_AZURE_AD_ALLOWED_GROUP=<your-group-id> \
  --build-arg VITE_API_BASE_URL=https://aks-health.contoso.com \
  --build-arg VITE_APP_NAME="AKS Health Dashboard" \
  frontend/

docker push <registry-name>.azurecr.io/aks-health-frontend:1.0.0
```

> **Note:** `VITE_API_BASE_URL` should match the hostname you configure in `ingress.host`. In local testing you can leave it blank — Vite's dev proxy handles the `/api` routing.

---

### Deploy with Helm

#### 1. Attach ACR to your AKS cluster

This allows the cluster to pull images from your registry without a pull secret:

```bash
az aks update \
  --name <cluster-name> \
  --resource-group <resource-group> \
  --attach-acr <registry-name>
```

#### 2. Connect kubectl to the cluster

```bash
az aks get-credentials \
  --resource-group <resource-group> \
  --name <cluster-name>
```

#### 3. Create a values override file

Create a file called `my-values.yaml` (do **not** commit it — it contains sensitive values):

```yaml
backend:
  image:
    repository: <registry-name>.azurecr.io/aks-health-backend
    tag: "1.0.0"
  config:
    azureTenantId: "00000000-0000-0000-0000-000000000000"       # your tenant ID
    azureSubscriptionIds: "11111111-1111-1111-1111-111111111111" # your subscription ID(s)
    azureFoundryEndpoint: "https://<project>.services.ai.azure.com/models"
    azureFoundryModel: "gpt-4o"
    azureAdAppClientId: "22222222-2222-2222-2222-222222222222"  # app registration client ID
    azureAdAllowedGroup: "33333333-3333-3333-3333-333333333333"  # AD group object ID
    frontendOrigin: "https://aks-health.contoso.com"            # your frontend URL

frontend:
  image:
    repository: <registry-name>.azurecr.io/aks-health-frontend
    tag: "1.0.0"

ingress:
  enabled: true
  className: "nginx"
  host: "aks-health.contoso.com"
  tls:
    enabled: true
    secretName: "aks-health-tls"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"

serviceAccount:
  create: true
```

#### 4. Install the Helm chart

```bash
helm install aks-health ./helm/aks-health \
  --namespace aks-health \
  --create-namespace \
  --values my-values.yaml
```

#### 5. Verify the deployment

```bash
# Watch pods come up
kubectl -n aks-health get pods -w

# Check services
kubectl -n aks-health get svc

# Check ingress
kubectl -n aks-health get ingress

# View backend logs
kubectl -n aks-health logs -l app.kubernetes.io/component=backend -f

# View frontend logs
kubectl -n aks-health logs -l app.kubernetes.io/component=frontend -f
```

#### Upgrading

After building a new image, upgrade the release:

```bash
helm upgrade aks-health ./helm/aks-health \
  --namespace aks-health \
  --values my-values.yaml \
  --set backend.image.tag=1.1.0 \
  --set frontend.image.tag=1.1.0
```

#### Uninstalling

```bash
helm uninstall aks-health --namespace aks-health
```

---

### Azure Workload Identity (Recommended)

Workload Identity lets the backend pod authenticate to Azure APIs using a Managed Identity — no client secret stored anywhere. This is the recommended production setup.

#### Step 1 — Enable Workload Identity on your AKS cluster

```bash
az aks update \
  --name <cluster-name> \
  --resource-group <resource-group> \
  --enable-workload-identity \
  --enable-oidc-issuer
```

Get the OIDC issuer URL:

```bash
az aks show \
  --name <cluster-name> \
  --resource-group <resource-group> \
  --query "oidcIssuerProfile.issuerUrl" \
  -o tsv
```

#### Step 2 — Create a Managed Identity

```bash
az identity create \
  --name aks-health-mcp-identity \
  --resource-group <resource-group>

# Save these values
CLIENT_ID=$(az identity show \
  --name aks-health-mcp-identity \
  --resource-group <resource-group> \
  --query clientId -o tsv)

OBJECT_ID=$(az identity show \
  --name aks-health-mcp-identity \
  --resource-group <resource-group> \
  --query principalId -o tsv)
```

#### Step 3 — Assign Azure RBAC roles to the Managed Identity

```bash
SUBSCRIPTION_ID=<your-subscription-id>

# Reader role for listing AKS clusters and resources
az role assignment create \
  --assignee $OBJECT_ID \
  --role Reader \
  --scope /subscriptions/$SUBSCRIPTION_ID

# Monitoring Reader for Azure Monitor metrics
az role assignment create \
  --assignee $OBJECT_ID \
  --role "Monitoring Reader" \
  --scope /subscriptions/$SUBSCRIPTION_ID
```

#### Step 4 — Create a Federated Credential

```bash
OIDC_ISSUER=<issuer-url-from-step-1>
NAMESPACE=aks-health

az identity federated-credential create \
  --name aks-health-federated \
  --identity-name aks-health-mcp-identity \
  --resource-group <resource-group> \
  --issuer "$OIDC_ISSUER" \
  --subject "system:serviceaccount:${NAMESPACE}:aks-health" \
  --audience api://AzureADTokenExchange
```

> The `--subject` must match `system:serviceaccount:<namespace>:<serviceaccount-name>`. The default service account name is `aks-health` (the Helm release name). Adjust if you used a different release name.

#### Step 5 — Configure Helm values

Add these to your `my-values.yaml`:

```yaml
serviceAccount:
  create: true
  annotations:
    azure.workload.identity/client-id: "<CLIENT_ID>"  # from Step 2

podAnnotations:
  azure.workload.identity/use: "true"

# Leave credentials empty — Workload Identity handles auth
backend:
  credentials:
    azureClientId: ""
    azureClientSecret: ""
    azureFoundryApiKey: ""
```

Upgrade the release to apply:

```bash
helm upgrade aks-health ./helm/aks-health \
  --namespace aks-health \
  --values my-values.yaml
```

---

### Service Principal Authentication

If Workload Identity is not available, use a Service Principal (created in [Step 4](#4-create-a-service-principal-optional--for-ci--robotic-access) of the Azure Setup):

```yaml
# my-values.yaml
backend:
  credentials:
    azureClientId: "<AZURE_CLIENT_ID>"
    azureClientSecret: "<AZURE_CLIENT_SECRET>"
    # azureFoundryApiKey: "<key>"  # optional
```

The chart will create a Kubernetes Secret holding these values and mount it into the backend pod automatically. **Do not commit `my-values.yaml` containing real secrets.**

Alternatively, if you manage secrets externally (Azure Key Vault CSI driver, External Secrets Operator, Sealed Secrets):

```yaml
backend:
  existingSecret: "my-external-secret-name"
  credentials: {}  # ignored when existingSecret is set
```

The external Secret must contain the keys: `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_FOUNDRY_API_KEY`.

---

### Helm Values Reference

The full list of configurable values with their defaults:

| Value | Default | Description |
|-------|---------|-------------|
| `backend.image.repository` | `""` | **Required.** Backend container image |
| `backend.image.tag` | `"1.0.0"` | Image tag |
| `backend.image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `backend.replicaCount` | `1` | Pod count (ignored when HPA enabled) |
| `backend.resources.requests.cpu` | `250m` | CPU request |
| `backend.resources.requests.memory` | `512Mi` | Memory request |
| `backend.resources.limits.cpu` | `"1"` | CPU limit |
| `backend.resources.limits.memory` | `1Gi` | Memory limit |
| `backend.autoscaling.enabled` | `false` | Enable HPA |
| `backend.autoscaling.minReplicas` | `1` | HPA min replicas |
| `backend.autoscaling.maxReplicas` | `3` | HPA max replicas |
| `backend.autoscaling.targetCPUUtilizationPercentage` | `70` | HPA CPU target |
| `backend.config.azureTenantId` | `""` | **Required.** Azure AD tenant GUID |
| `backend.config.azureSubscriptionIds` | `""` | **Required.** Comma-separated subscription GUIDs |
| `backend.config.azureFoundryEndpoint` | `""` | **Required.** Azure AI Foundry endpoint URL |
| `backend.config.azureFoundryModel` | `"gpt-4o"` | Foundry model deployment name |
| `backend.config.azureAdAppClientId` | `""` | **Required.** App registration client ID |
| `backend.config.frontendOrigin` | `""` | **Required.** Frontend URL (CORS allow-origin) |
| `backend.config.logLevel` | `"INFO"` | Log level |
| `backend.config.logFormat` | `"json"` | `json` or `console` |
| `backend.credentials.azureClientSecret` | `""` | App registration client secret (for OBO) |
| `backend.credentials.azureFoundryApiKey` | `""` | Azure AI Foundry API key |
| `backend.existingSecret` | `""` | Name of pre-existing Kubernetes Secret |
| `frontend.enabled` | `true` | Deploy the frontend |
| `frontend.image.repository` | `""` | **Required.** Frontend container image |
| `frontend.image.tag` | `"1.0.0"` | Image tag |
| `frontend.replicaCount` | `1` | Pod count |
| `ingress.enabled` | `false` | Create Ingress resource |
| `ingress.className` | `"nginx"` | IngressClass name |
| `ingress.annotations` | `{}` | Extra annotations (e.g. cert-manager) |
| `ingress.host` | `""` | **Required when ingress enabled.** Hostname |
| `ingress.tls.enabled` | `false` | Enable TLS on Ingress |
| `ingress.tls.secretName` | `""` | TLS secret name (auto-generated if blank) |
| `serviceAccount.create` | `true` | Create ServiceAccount |
| `serviceAccount.annotations` | `{}` | Annotations (Workload Identity client-id) |
| `rbac.create` | `true` | Create read-only ClusterRole + binding |
| `imagePullSecrets` | `[]` | Image pull secrets list |
| `podAnnotations` | `{}` | Annotations on all pods |
| `podLabels` | `{}` | Extra labels on all pods |

---

## Running Tests

```bash
make test
```

Expected output: all tests pass (MCP-server-specific tests were removed in v2.0.0 when the official binary replaced the custom Python server).

To also see code coverage:

```bash
make test-cov
```

---

## Authentication Reference

### SSO Portal — Authentication flow

The React frontend uses **MSAL PKCE redirect flow**. After login the FastAPI backend:

1. Validates the JWT signature using the tenant's JWKS (cached 1 h).
2. Confirms the audience matches `AZURE_AD_APP_CLIENT_ID`.
3. **No group check** — any successfully authenticated Azure AD user is allowed.

The backend then performs an **On-Behalf-Of (OBO)** token exchange:

4. Exchanges the user's app token for an Azure Resource Manager token.
5. The ARM token confirms the caller's identity in logs. It is also available for future use (e.g. per-user Azure SDK calls outside aks-mcp).

> **Note on credential model:** The `aks-mcp` binary authenticates using the **server's identity** (Service Principal env vars, Workload Identity, or `az login`). It does not accept a per-request injected token. Azure RBAC enforcement therefore applies to the server's managed identity, not to each individual user. See the [Session Isolation — Known Limitations](#session-isolation) section.

### Azure credential chain in `aks-mcp`

The binary follows the standard Azure SDK `DefaultAzureCredential` chain:

| Priority | Credential | When used |
|----------|-----------|-----------|
| 1 | Service Principal | `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` set |
| 2 | Workload Identity | `AZURE_FEDERATED_TOKEN_FILE` set (AKS pod with Workload Identity) |
| 3 | Managed Identity | Running on Azure VM / AKS with system-assigned identity |
| 4 | Azure CLI | Local dev: user has run `az login` |

Required Azure RBAC roles on the **server's identity** (Service Principal or Managed Identity):
- `Reader` on the subscription or resource group
- `Monitoring Reader` on the subscription (for Azure Monitor metrics)

### Azure AI Foundry — agent LLM

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | API key (`AzureKeyCredential`) | `AZURE_FOUNDRY_API_KEY` is set |
| 2 | Server-level Azure AD credential | Key not set |

### Kubernetes

The `aks-mcp` binary discovers kubeconfig the same way `kubectl` does:

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | In-cluster ServiceAccount | `KUBERNETES_SERVICE_HOST` set (running as a pod) |
| 2 | Kubeconfig | `KUBECONFIG` env var or `~/.kube/config` |

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
| **AI Firewall** | Two-stage input guard on every query: fast regex block (prompt injection, credential extraction, destructive ops) followed by LLM relevance classifier. Fails open — infra failures never block legitimate queries. See `agents/firewall.py`. |
| **Hallucination Reviewer** | LLM second-pass cross-checks the synthesised answer against raw tool data. Corrects fabricated cluster names, counts, or statuses and appends a transparency note when changes are made. See `agents/reviewer.py`. |
| **Read-only access level** | aks-mcp binary started with `--access-level readonly`; write tools are unavailable regardless of agent instructions. `AKS_MCP_ACCESS_LEVEL` can only be raised by explicit operator config change. |
| **Process isolation** | Every agent invocation spawns a fresh `aks-mcp` child process. Process terminates at end of request. No in-memory state shared between users. |
| **Log context isolation** | `structlog` uses `contextvars` (asyncio-task-local). `query_id` and `user_oid` are bound at request start and unbound in `finally`. `clear_contextvars()` is never called (would drop middleware's `request_id`). |
| **Child process env filtering** | `AZURE_FOUNDRY_API_KEY`, `AZURE_FOUNDRY_ENDPOINT`, `AZURE_FOUNDRY_MODEL`, and `AZURE_ARM_TOKEN` are stripped before the child env is passed to aks-mcp (principle of least privilege). |
| **Error message sanitisation** | Internal exception details are logged server-side only; the SSE stream returns a generic message to the client. |
| Credential isolation | Credentials loaded from env at startup; MCP tool arguments never accept secrets. |
| Secret masking | `pydantic.SecretStr` for all secret fields; structlog scrubs known-secret key names in log output. |
| Audit logging | Every tool call and HTTP request logged with tool name, UPN, OID (no secret values). |
| Concurrency cap | `asyncio.Semaphore(5)` limits concurrent agent calls; prevents resource exhaustion. |
| Azure RBAC enforcement | Azure RBAC roles on the server's identity determine which subscriptions and resources are accessible. |
| CORS | Single configured `FRONTEND_ORIGIN`; no wildcards. |
| Token validation | RS256 + JWKS cache (1 h TTL, bounded to 10 tenants); audience accepts `<clientId>` and `api://<clientId>`; separate tenant keys cannot mix. |

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

### Agent returns empty results or "403 Forbidden" from Azure tools

The signed-in user's Azure identity does not have Azure RBAC access to the subscription. Assign the `Reader` and `Monitoring Reader` roles to the user (or their AD group) on the subscription — see [Step 4](#4-assign-azure-rbac-roles-to-users).

### OBO token exchange fails ("AADSTS…" error in backend logs)

Common causes:
- `AZURE_CLIENT_SECRET` is wrong or expired. Create a new secret on the app registration (Step 3b) and update `.env`.
- The app registration does not have **Azure Service Management → user_impersonation** delegated permission granted. Go to API permissions and grant admin consent (Step 3c).
- The user's token audience does not match the backend. Ensure the frontend requests `api://<AZURE_AD_APP_CLIENT_ID>/user_impersonation` scope.

### `Missing VITE_AZURE_AD_TENANT_ID or VITE_AZURE_AD_CLIENT_ID`

You forgot to create `frontend/.env`. Run:

```bash
cp frontend/.env.example frontend/.env
# then edit frontend/.env with your values
```

### Agent returns "Tool execution failed"

Check that:
1. The `aks-mcp` binary is on PATH or `AKS_MCP_BINARY` in `.env` points to the correct path. Verify: `aks-mcp --version` (or `./bin/aks-mcp --version`).
2. Azure CLI and kubectl are installed and on PATH — the binary shells out to them.
3. The Azure credential (Service Principal or `az login`) has the `Reader` and `Monitoring Reader` roles.
4. The kubeconfig is pointing to the right cluster: `kubectl get nodes`.

### "Query not allowed" error returned to the browser

The AI firewall blocked the query before it reached the agent. Two stages run in sequence:

1. **Fast regex check** — pattern-matched against known bad inputs (prompt injection phrases, credential extraction requests, destructive kubectl commands). These are always blocked regardless of LLM availability.
2. **LLM relevance check** — an Azure AI Foundry call classifies whether the query is related to AKS/Kubernetes health. Unrelated queries (weather, cooking, etc.) are blocked here.

If a legitimate AKS query is blocked by the LLM classifier, rephrase it to make the Kubernetes/Azure context explicit (e.g. "What is the pod status in my AKS cluster?" instead of just "What is running?").

The firewall **fails open** — if Azure AI Foundry is unreachable, the query passes through so legitimate work is never interrupted.

---

### `403 Forbidden` on `/api/auth/me` in Postman / curl

The request is missing an `Authorization: Bearer <token>` header. Every API call requires a valid Azure AD token.

### Backend starts but CORS errors appear in the browser console

`FRONTEND_ORIGIN` in `.env` does not match the URL your browser is using. For local development it must be exactly `http://localhost:5173` (no trailing slash).

### Helm: `backend.config.azureTenantId is required` (or similar)

You installed the chart without setting the required values. Make sure your `my-values.yaml` sets all fields marked **Required** in the [Helm Values Reference](#helm-values-reference), or pass them with `--set`:

```bash
helm install aks-health ./helm/aks-health \
  --set backend.config.azureTenantId=00000000-... \
  ...
```

### Helm: backend pod is `CrashLoopBackOff`

Check the logs:

```bash
kubectl -n aks-health logs -l app.kubernetes.io/component=backend --previous
```

Common causes:
- Missing or wrong Azure credentials (run `kubectl -n aks-health get secret` to confirm the secret exists).
- Workload Identity annotation missing (`azure.workload.identity/use: "true"` on the pod).
- Wrong `AZURE_FOUNDRY_ENDPOINT` — the URL must end with `/models`.

### Helm: frontend shows blank page after Ingress is up

The frontend `VITE_*` variables were not set at Docker build time. Rebuild the frontend image with the correct `--build-arg` values and push a new tag, then upgrade the Helm release.

### Helm: `ImagePullBackOff`

The cluster cannot pull the image. Check:
1. ACR is attached to the cluster: `az aks show ... | grep acrProfile`
2. Image tag matches what was pushed: `docker images | grep aks-health`
3. If using a pull secret, it is listed under `imagePullSecrets` in `my-values.yaml`.

---

## Project Structure

```
aks-health-mcp/
├── bin/                           # Downloaded aks-mcp binary (git-ignored)
│   └── aks-mcp                    # Official Microsoft Go binary (make install-aks-mcp)
│
├── server/                        # Shared config / auth / logging utilities
│   ├── config.py                  # Pydantic-settings: AKS_MCP_BINARY, Azure, Foundry
│   ├── logging_config.py          # structlog structured logging setup
│   └── auth/
│       └── credentials.py         # Azure credential chain factory (CLI / Workload Identity)
│
├── agents/                        # Azure AI Foundry multi-agent framework
│   ├── base.py                    # Base class: spawns aks-mcp via stdio, conversation loop
│   │                              # tool_prefixes tuple filtering, child-env secret stripping
│   ├── root_agent.py              # Root orchestrator (concurrent asyncio sub-agent dispatch)
│   ├── firewall.py                # AI input guard (regex + LLM classifier)
│   ├── reviewer.py                # Hallucination reviewer (LLM grounding check)
│   └── task_agents/
│       ├── azure_health_agent.py  # tool_prefixes: az_  aks_  get_aks_  call_az
│       └── cluster_health_agent.py# tool_prefixes: call_kubectl  call_helm  collect_  inspektor_
│
├── api/                           # FastAPI backend (SSO portal bridge)
│   ├── main.py                    # App factory, CORS, request logging, health probes
│   ├── config.py                  # ApiSettings (extends server Settings)
│   ├── auth/
│   │   └── azure_ad.py            # JWT validation (RS256), JWKS cache, OBO exchange
│   └── routes/
│       ├── auth.py                # GET /api/auth/me
│       └── agent.py               # POST /api/agent/query (SSE stream, contextvars isolation)
│
├── frontend/                      # React sysadmin portal
│   ├── src/
│   │   ├── authConfig.ts          # MSAL config (PKCE, SessionStorage)
│   │   ├── App.tsx                # Router + MsalProvider
│   │   ├── api/agentApi.ts        # Typed API client (fetch + SSE parser)
│   │   ├── hooks/
│   │   │   ├── useGroupAuth.ts    # Silent token + /auth/me validation
│   │   │   └── useAgentQuery.ts   # SSE lifecycle: idle→loading→done
│   │   └── components/
│   │       ├── ProtectedRoute.tsx # Auth gate
│   │       ├── Dashboard.tsx      # Main layout + history sidebar
│   │       ├── HealthReport.tsx   # Markdown renderer + skeleton loading
│   │       ├── QueryInput.tsx     # Textarea + suggested-query chips
│   │       ├── LoginPage.tsx      # Microsoft sign-in card
│   │       ├── AccessDenied.tsx   # Shown for unauthorised users
│   │       └── Header.tsx         # User info + sign-out
│   ├── package.json
│   ├── vite.config.ts             # Dev proxy → localhost:8000
│   └── tailwind.config.js
│
├── tests/
│   ├── conftest.py                # Env stubs, lru_cache clear between tests
│   ├── test_config.py             # Settings validation, SecretStr masking
│   ├── test_agents.py             # Agent tool_prefixes, tool filtering, mock MCP sessions
│   ├── test_api_auth.py           # JWT validation, JWKS cache isolation, CORS
│   ├── test_firewall.py           # AI firewall: regex patterns + LLM classifier mock
│   └── test_reviewer.py           # Hallucination reviewer: grounding checks
│
├── Dockerfile                     # 3-stage build: aks-mcp binary + Python deps + runtime
├── .dockerignore
├── deploy/
│   └── nginx.conf                 # nginx config for frontend container
├── frontend/
│   └── Dockerfile                 # Frontend multi-stage Docker build (nginx-unprivileged)
├── helm/
│   └── aks-health/
│       ├── Chart.yaml
│       ├── values.yaml            # Full defaults + inline docs
│       └── templates/             # ServiceAccount, RBAC, ConfigMap, Secret, Deployment,
│                                  # Service, HPA, Ingress
├── pyproject.toml                 # v2.0.0 – mcp client + azure-identity + foundry only
├── Makefile                       # install-aks-mcp, run-api, run-agent, test, lint
├── .env.example                   # Copy to .env and fill in values
└── frontend/.env.example          # Copy to frontend/.env and fill in values
```
