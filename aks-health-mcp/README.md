# AKS Health MCP Server

Production-grade **Model Context Protocol (MCP) server** for monitoring Azure Kubernetes Service (AKS) health from both the **Azure control plane** and the **live in-cluster Kubernetes API**.

Includes a multi-agent framework with a **Root Orchestrator** that coordinates two specialised task agents, all communicating through the MCP server.

---

## Architecture

```
User Query
    │
    ▼
┌─────────────────────────────────────┐
│          Root Agent (Claude)        │  ← Orchestrates, synthesises
│  tools: query_azure_health          │
│          query_cluster_health       │
└────────────┬─────────────┬──────────┘
             │             │   (concurrent)
             ▼             ▼
  ┌──────────────┐  ┌──────────────────┐
  │ Azure Health │  │ Cluster Health   │
  │    Agent     │  │     Agent        │
  │ (aks_* tools)│  │ (k8s_* tools)   │
  └──────┬───────┘  └────────┬─────────┘
         │                   │
         └─────────┬─────────┘
                   ▼
        ┌──────────────────────┐
        │  AKS Health MCP      │  ← Strict read-only
        │     Server           │
        │  ┌────────────────┐  │
        │  │ Azure AKS tools│  │  → Azure Resource Manager API
        │  │ K8s tools      │  │  → Kubernetes API
        │  └────────────────┘  │
        └──────────────────────┘
```

---

## MCP Tools

### Azure (control-plane) — `aks_*`

| Tool | Description |
|------|-------------|
| `aks_list_clusters` | List AKS clusters in a subscription / resource group |
| `aks_get_cluster_detail` | Full cluster config, network profile, OIDC settings |
| `aks_get_node_pools` | Node pool VM sizes, autoscaling, provisioning state |
| `aks_get_upgrade_profile` | Available K8s upgrades (GA and preview) |
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

### Service Principal (Robotic / CI)
Set all three env vars:
```bash
AZURE_TENANT_ID=<guid>
AZURE_CLIENT_ID=<app-id>
AZURE_CLIENT_SECRET=<secret>
```
The SP needs the following **read-only** Azure RBAC roles:
- `Reader` on the subscription or resource group
- `Monitoring Reader` on the subscription (for Azure Monitor metrics)

### User / Local Development
Run `az login` before starting the server. The server uses `AzureCliCredential` automatically.

### In-Cluster (Workload Identity / Pod Identity)
Set `K8S_IN_CLUSTER=true` or ensure `KUBERNETES_SERVICE_HOST` is set.
The server uses the pod's ServiceAccount token.

Required Kubernetes RBAC (read-only `ClusterRole`):
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

---

## Quick Start

```bash
# 1. Clone and install
git clone <repo>
cd aks-health-mcp
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env with your Azure tenant ID and auth details

# 3. Run tests
make test

# 4. Start the MCP server (stdio – for agent use)
make run-stdio

# 5. Ask a health question via the root agent
make run-agent QUERY="What is the overall health of my AKS clusters?"
```

---

## Running the Root Agent

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

## Security Notes

- **Read-only contract**: No tool exposes any create/update/delete/patch operation. This is enforced at the SDK call level, not just by naming convention.
- **Credential isolation**: Credentials are loaded from the server environment. Tool arguments never accept credentials.
- **Secret masking**: Pydantic `SecretStr` fields and the structlog `_scrub_sensitive` processor prevent secrets from appearing in logs.
- **Audit logging**: Every tool call emits a structured audit log entry with tool name and non-sensitive arguments.
- **Rate limiting**: Tenacity retry logic with exponential back-off prevents thundering-herd issues against Azure APIs.
- **SSE transport**: When `MCP_TRANSPORT=sse`, bind only to `127.0.0.1` (default) or use a reverse proxy with TLS termination for production.

---

## Project Structure

```
aks-health-mcp/
├── server/
│   ├── main.py              # FastMCP server, tool registration
│   ├── config.py            # Pydantic-settings configuration
│   ├── logging_config.py    # Structured logging (structlog)
│   ├── auth/
│   │   └── credentials.py   # Azure + K8s credential factories
│   └── tools/
│       ├── azure_aks.py     # Azure AKS read tools
│       └── cluster_k8s.py  # Kubernetes read tools
├── agents/
│   ├── base.py              # Base MCP-connected agent
│   ├── root_agent.py        # Root orchestrator
│   └── task_agents/
│       ├── azure_health_agent.py   # Azure task agent
│       └── cluster_health_agent.py # Cluster task agent
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_azure_tools.py
│   ├── test_cluster_tools.py
│   └── test_mcp_server.py
├── pyproject.toml
├── Makefile
└── .env.example
```
