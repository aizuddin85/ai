# AKS Health MCP Server

Production-grade **Model Context Protocol (MCP) server** for monitoring Azure Kubernetes Service (AKS) health from both the **Azure control plane** and the **live in-cluster Kubernetes API**.

Includes a **multi-agent framework** powered by **Azure AI Foundry** and a **React sysadmin portal** with Azure AD SSO. Access to Azure resources is governed by each user's **Azure RBAC role assignments** — no hard-coded group configuration required.

---

## Table of Contents

1. [Architecture](#architecture)
2. [MCP Tools](#mcp-tools)
3. [Prerequisites](#prerequisites)
4. [Azure Setup](#azure-setup)
   - [Find Your Tenant ID and Subscription ID](#1-find-your-tenant-id-and-subscription-id)
   - [Create an App Registration](#2-create-an-app-registration)
   - [Configure the App Registration](#3-configure-the-app-registration)
   - [Assign Azure RBAC Roles to Users](#4-assign-azure-rbac-roles-to-users)
   - [Set Up Azure AI Foundry](#5-set-up-azure-ai-foundry)
5. [Installation](#installation)
6. [Configuration](#configuration)
   - [Backend `.env`](#backend-env)
   - [Frontend `frontend/.env`](#frontend-frontendenv)
7. [Running the Stack](#running-the-stack)
8. [Kubernetes Deployment (Docker + Helm)](#kubernetes-deployment-docker--helm)
   - [Prerequisites for Kubernetes](#prerequisites-for-kubernetes)
   - [Build and Push Docker Images](#build-and-push-docker-images)
   - [Deploy with Helm](#deploy-with-helm)
   - [Azure Workload Identity (Recommended)](#azure-workload-identity-recommended)
   - [Service Principal Authentication](#service-principal-authentication)
   - [Helm Values Reference](#helm-values-reference)
9. [Running Tests](#running-tests)
10. [Authentication Reference](#authentication-reference)
11. [Kubernetes RBAC](#kubernetes-rbac)
12. [Security Controls](#security-controls)
13. [Troubleshooting](#troubleshooting)
14. [Project Structure](#project-structure)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Sysadmin Browser                         │
│              React + MSAL (Azure AD SSO / PKCE)              │
└────────────────────────┬─────────────────────────────────────┘
                         │  Bearer token
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Backend                            │
│  JWT validation · OBO token exchange · SSE streaming         │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  AI Firewall  (fast regex + LLM classifier)         │     │
│  │  Blocks: prompt injection · credential extraction   │     │
│  │          destructive ops · off-topic queries         │     │
│  └─────────────────────────────────────────────────────┘     │
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
                          │
                          ▼
          ┌───────────────────────────────┐
          │  Hallucination Reviewer       │
          │  LLM cross-checks answer vs   │
          │  raw tool data; corrects and  │
          │  flags fabricated facts       │
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
# ── Azure Tenant ──────────────────────────────────────────────────────────────
# Your Azure AD Tenant ID (GUID from Step 1)
AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000

# One or more Azure subscription IDs to query (comma-separated).
# Single subscription:
AZURE_SUBSCRIPTION_IDS=11111111-1111-1111-1111-111111111111
# Multiple subscriptions (aks_list_clusters and health events iterate all of them):
# AZURE_SUBSCRIPTION_IDS=11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222

# ── App Registration ──────────────────────────────────────────────────────────
# Application (client) ID of your app registration (from Step 2)
AZURE_AD_APP_CLIENT_ID=22222222-2222-2222-2222-222222222222

# Client secret of the SAME app registration (from Step 3b).
# Used for the On-Behalf-Of (OBO) token exchange: Azure API calls run as
# the signed-in user, so their Azure RBAC determines what they can access.
# Leave blank to fall back to `az login` (local dev) or Workload Identity
# (AKS pod). Without this, all users see the same resources as the server.
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

Expected output: **80 tests pass**.

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
5. Injects the ARM token into the MCP server subprocess.
6. All Azure SDK calls run as the signed-in user — Azure RBAC determines what is returned.

### Azure credential chain in the MCP server

| Priority | Credential | When used |
|----------|-----------|-----------|
| 1 | `StaticTokenCredential` (OBO ARM token) | `AZURE_ARM_TOKEN` set (per-request, injected by API) |
| 2 | `AzureCliCredential` | Local dev fallback: user has run `az login` |
| 3 | `ManagedIdentityCredential` | AKS pod with Workload Identity (no OBO configured) |

Required Azure RBAC roles on the **user's identity** (or their AD groups):
- `Reader` on the subscription or resource group
- `Monitoring Reader` on the subscription (for Azure Monitor metrics)

### Azure AI Foundry — agent LLM

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | API key (`AzureKeyCredential`) | `AZURE_FOUNDRY_API_KEY` is set |
| 2 | Server-level Azure AD credential | Key not set (OBO does not apply here) |

### Kubernetes

| Priority | Method | When used |
|----------|--------|-----------|
| 1 | In-cluster ServiceAccount | `KUBERNETES_SERVICE_HOST` set or `K8S_IN_CLUSTER=true` |
| 2 | Kubeconfig | `KUBECONFIG` env or `~/.kube/config` |

Kubernetes tools always use the server's own ServiceAccount — they are not subject to OBO.

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
| Read-only MCP tools | No create/update/delete/patch SDK call is ever made; enforced by `test_no_write_tools_registered` |
| Credential isolation | Credentials loaded from env only; tool arguments never accept secrets |
| Secret masking | `pydantic.SecretStr` + structlog `_scrub_sensitive` processor on all log output |
| Audit logging | Every tool call and HTTP request logged with tool name, UPN, OID (no secret values) |
| Azure RBAC enforcement | OBO flow: Azure API calls run as the user; Azure evaluates role assignments at query time |
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
1. The MCP server can start on its own: `python -m server.main` (it will block on stdin — press Ctrl+C to exit; if it starts without errors, the server is working).
2. The Azure credential has the `Reader` and `Monitoring Reader` roles.
3. The kubeconfig is pointing to the right cluster.

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
│   ├── firewall.py                # AI input guard (regex + LLM classifier)
│   ├── reviewer.py                # Hallucination reviewer (LLM grounding check)
│   └── task_agents/
│       ├── azure_health_agent.py  # Scoped to aks_* tools
│       └── cluster_health_agent.py# Scoped to k8s_* tools
│
├── api/                           # FastAPI backend (SSO portal bridge)
│   ├── main.py                    # App factory, CORS, request logging, probes
│   ├── config.py                  # ApiSettings (extends server Settings)
│   ├── auth/
│   │   └── azure_ad.py            # JWT validation, JWKS cache
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
│   │   │   ├── useGroupAuth.ts    # Silent token + /auth/me validation
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
├── tests/                         # 80 tests
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_mcp_server.py
│   ├── test_azure_tools.py
│   ├── test_cluster_tools.py
│   ├── test_agents.py
│   ├── test_api_auth.py
│   ├── test_firewall.py           # AI firewall (13 tests)
│   └── test_reviewer.py           # Hallucination reviewer (7 tests)
│
├── Dockerfile                         # Backend multi-stage Docker build
├── .dockerignore
├── deploy/
│   └── nginx.conf                     # nginx config for frontend container
├── frontend/
│   └── Dockerfile                     # Frontend multi-stage Docker build (nginx-unprivileged)
├── helm/
│   └── aks-health/
│       ├── Chart.yaml
│       ├── values.yaml                # Full defaults + inline docs
│       └── templates/
│           ├── _helpers.tpl
│           ├── NOTES.txt
│           ├── serviceaccount.yaml
│           ├── rbac.yaml              # Read-only ClusterRole + binding
│           ├── configmap.yaml         # Non-sensitive config
│           ├── secret.yaml            # Credentials (conditional)
│           ├── backend-deployment.yaml
│           ├── backend-service.yaml
│           ├── backend-hpa.yaml       # HPA (conditional)
│           ├── frontend-deployment.yaml
│           ├── frontend-service.yaml
│           └── ingress.yaml           # Ingress (conditional)
├── pyproject.toml                     # deps: base + api + dev extras
├── Makefile
├── .env.example                       # Copy to .env and fill in values
└── frontend/.env.example              # Copy to frontend/.env and fill in values
```
