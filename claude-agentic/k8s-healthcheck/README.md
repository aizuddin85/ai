# Kubernetes Health Check Agent

An agentic AI that autonomously inspects your Kubernetes cluster and produces a structured health report. Powered by **Claude Opus 4.6** with adaptive thinking and tool use.

## How it works

The agent runs a multi-turn conversation loop with Claude. On each turn, Claude decides which `kubectl` tool to call next — no hardcoded inspection order. It keeps calling tools until it has enough data to write a full Markdown report.

```
User prompt
    │
    ▼
Claude (thinking) ──► tool_use ──► kubectl runs locally ──► tool_result
    │                                                              │
    └──────────────────────────────────────────────────────────────┘
                        repeat until end_turn
    │
    ▼
Markdown health report printed to stdout
```

### Agent strategy (system prompt)

1. Read the current kubeconfig context (cluster, server, user)
2. Check all **nodes** for readiness
3. List all **namespaces**
4. For each namespace:
   - Pod status — flags non-Running/non-Completed pods and high restart counts
   - Deployment health — flags unavailable replicas
   - Services
   - Warning events
   - `describe` any unhealthy resource for details
5. Write a **Markdown report** with:
   - Executive summary (Healthy / Degraded / Critical)
   - Nodes section
   - Per-namespace breakdown
   - Prioritised issues (Critical / Warning / Info)
   - Recommendations

### Available tools

| Tool | Description |
|---|---|
| `get_current_context` | Active context, server URL, user, namespace |
| `check_nodes` | Node readiness and kubelet version |
| `list_namespaces` | All namespaces in the cluster |
| `check_pods` | Pod status, readiness, restart count (per namespace) |
| `check_deployments` | Desired vs available replicas (per namespace) |
| `check_services` | Service type, cluster IP, ports (per namespace) |
| `check_events` | Warning-level events only (per namespace) |
| `describe_resource` | Deep-dive on any unhealthy pod, deployment, or node |

## Prerequisites

- Python 3.10+
- `kubectl` installed and on `PATH`
- A valid `~/.kube/config` pointing at the target cluster
- An Anthropic API key

## Getting an Anthropic API key

1. Sign in (or create an account) at [console.anthropic.com](https://console.anthropic.com)
2. Go to **API Keys** in the left sidebar
3. Click **Create Key**, give it a name, and copy the value — it starts with `sk-ant-`

> Keep your key secret. Never commit it to version control.

## Setup

```bash
# 1. Clone / navigate to this directory
cd k8s-healthcheck

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
```

### Option A — environment variable (recommended)

```bash
# macOS / Linux (current shell session)
export ANTHROPIC_API_KEY="sk-ant-..."

# Add to your shell profile to persist across sessions
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc   # zsh
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc  # bash
```

```powershell
# Windows PowerShell (current session)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Persist for your user account
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","sk-ant-...","User")
```

### Option B — `.env` file

Create a `.env` file in this directory (already git-ignored if you use the snippet below):

```bash
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > .env
echo '.env' >> .gitignore
```

Then load it before running:

```bash
set -a && source .env && set +a
python3 k8s_healthcheck.py
```

### Verify the key is set

```bash
echo $ANTHROPIC_API_KEY          # should print your key
python3 -c "import anthropic; anthropic.Anthropic().models.list(); print('OK')"
```

## Usage

```bash
python3 k8s_healthcheck.py
```

The agent prints each tool call and its (truncated) output as it runs, then outputs the final Markdown report. Redirect to a file to save the report:

```bash
python3 k8s_healthcheck.py > report.md
```

### Switching clusters

The agent uses whichever context `kubectl config current-context` returns. Switch context before running:

```bash
kubectl config use-context my-other-cluster
python3 k8s_healthcheck.py
```

## Project structure

```
k8s-healthcheck/
├── k8s_healthcheck.py   # Agent + tool implementations
├── requirements.txt     # anthropic SDK
└── README.md
```

## Sample terminal output

```
$ python3 k8s_healthcheck.py

🔍  Starting Kubernetes health-check agent …

── Turn 1 ──────────────────────────────────
🔧  Tool: get_current_context  args={}
Context:   staging-cluster
Server:    https://192.168.1.100:6443
User:      admin
Namespace: default

🔧  Tool: check_nodes  args={}
NAME              STATUS   ROLES    AGE                    VERSION
node-master-01    Ready    master   2024-11-01T08:00:00Z   v1.31.2
node-worker-01    Ready    <none>   2024-11-01T08:05:00Z   v1.31.2
node-worker-02    Ready    <none>   2024-11-01T08:05:00Z   v1.31.2

🔧  Tool: list_namespaces  args={}
NAME              STATUS   AGE
default           Active   2024-11-01T08:00:00Z
kube-system       Active   2024-11-01T08:00:00Z
kube-public       Active   2024-11-01T08:00:00Z
kube-node-lease   Active   2024-11-01T08:00:00Z
monitoring        Active   2024-11-10T09:30:00Z
production        Active   2024-11-12T14:00:00Z

── Turn 2 ──────────────────────────────────
🔧  Tool: check_pods  args={"namespace": "default"}
(no output)

🔧  Tool: check_deployments  args={"namespace": "default"}
(no output)

🔧  Tool: check_events  args={"namespace": "default"}
(no output)

── Turn 3 ──────────────────────────────────
🔧  Tool: check_pods  args={"namespace": "kube-system"}
NAME                                       READY   STATUS    RESTARTS   AGE     IP
coredns-5dd5756b68-g4qtr                   1/1     Running   0          40d     10.244.0.3
coredns-5dd5756b68-r9kpz                   1/1     Running   0          40d     10.244.0.4
etcd-node-master-01                        1/1     Running   2          40d     192.168.1.100
kube-apiserver-node-master-01              1/1     Running   2          40d     192.168.1.100
kube-controller-manager-node-master-01     1/1     Running   3          40d     192.168.1.100
kube-proxy-7fzqx                           1/1     Running   0          40d     192.168.1.101
kube-proxy-9xklt                           1/1     Running   0          40d     192.168.1.102
kube-scheduler-node-master-01              1/1     Running   3          40d     192.168.1.100

🔧  Tool: check_deployments  args={"namespace": "kube-system"}
NAME      DESIRED   AVAILABLE   READY   AGE
coredns   2         2           2       2024-11-01T08:00:00Z

🔧  Tool: check_events  args={"namespace": "kube-system"}
(no output)

── Turn 4 ──────────────────────────────────
🔧  Tool: check_pods  args={"namespace": "monitoring"}
NAME                                   READY   STATUS             RESTARTS   AGE     IP
alertmanager-main-0                    2/2     Running            0          28d     10.244.1.15
grafana-7d9f5dbb6c-xk2pq               1/1     Running            0          28d     10.244.2.20
prometheus-k8s-0                       2/2     Running            0          28d     10.244.1.16
prometheus-operator-6bc9b4b5d4-9vhzm   1/1     Running            0          28d     10.244.2.21

🔧  Tool: check_deployments  args={"namespace": "monitoring"}
NAME                  DESIRED   AVAILABLE   READY   AGE
grafana               1         1           1       2024-11-10T09:30:00Z
prometheus-operator   1         1           1       2024-11-10T09:30:00Z

🔧  Tool: check_events  args={"namespace": "monitoring"}
(no output)

── Turn 5 ──────────────────────────────────
🔧  Tool: check_pods  args={"namespace": "production"}
NAME                          READY   STATUS             RESTARTS   AGE    IP
api-server-7d8f9b6c5-2xkpq    1/1     Running            0          3d     10.244.1.30
api-server-7d8f9b6c5-9vhzm    1/1     Running            0          3d     10.244.2.31
frontend-5b9c7d4f8-hq3rt      0/1     CrashLoopBackOff   18         2h     10.244.1.32
worker-6c4d8e7b9-xk2lp        1/1     Running            0          3d     10.244.2.33
cache-redis-0                 1/1     Running            0          3d     10.244.1.34

🔧  Tool: check_deployments  args={"namespace": "production"}
NAME         DESIRED   AVAILABLE   READY   AGE
api-server   2         2           2       2024-11-12T14:00:00Z
frontend     1         0           0       2024-11-17T10:00:00Z
worker       1         1           1       2024-11-12T14:00:00Z

🔧  Tool: check_events  args={"namespace": "production"}
LAST SEEN   TYPE      REASON              OBJECT                         MESSAGE
4m          Warning   BackOff             Pod/frontend-5b9c7d4f8-hq3rt   Back-off restarting failed container
9m          Warning   Failed              Pod/frontend-5b9c7d4f8-hq3rt   Error: failed to pull image "myregistry/frontend:v2.1.0": not found
15m         Warning   FailedScheduling    Pod/worker-6c4d8e7b9-xk2lp     0/3 nodes are available: insufficient memory

🔧  Tool: describe_resource  args={"resource_type": "pod", "resource_name": "frontend-5b9c7d4f8-hq3rt", "namespace": "production"}
Name:         frontend-5b9c7d4f8-hq3rt
Namespace:    production
Status:       Running (CrashLoopBackOff)
Image:        myregistry/frontend:v2.1.0
Reason:       ImagePullBackOff
Message:      Back-off pulling image "myregistry/frontend:v2.1.0"
Events:
  Warning  Failed     9m    kubelet  Failed to pull image: rpc error: repository not found
  Warning  BackOff    4m    kubelet  Back-off restarting failed container…

── Turn 6 ──────────────────────────────────

# Kubernetes Cluster Health Report

**Generated:** 2024-11-19
**Cluster:** staging-cluster (`https://192.168.1.100:6443`)
**Context:** staging-cluster / admin

---

## Executive Summary

**Overall Status: ⚠️ DEGRADED**

The cluster is partially healthy. 2 of 3 nodes are fully operational. Most workloads are running normally, however a critical issue was found in the `production` namespace: the `frontend` deployment has 0 available replicas due to an invalid container image tag.

---

## Nodes

| Node | Status | Role | Version |
|---|---|---|---|
| node-master-01 | ✅ Ready | master | v1.31.2 |
| node-worker-01 | ✅ Ready | worker | v1.31.2 |
| node-worker-02 | ✅ Ready | worker | v1.31.2 |

All 3 nodes are Ready. No node-level issues detected.

---

## Namespaces

### default
No workloads deployed.

### kube-system
All 8 system pods are Running. CoreDNS deployment is fully available (2/2). No warning events.

### kube-node-lease / kube-public
No workloads. No issues.

### monitoring
All monitoring stack pods (Prometheus, Grafana, Alertmanager) are Running. No warning events.

### production ⚠️

**Pods**

| Pod | Status | Restarts |
|---|---|---|
| api-server-7d8f9b6c5-2xkpq | ✅ Running | 0 |
| api-server-7d8f9b6c5-9vhzm | ✅ Running | 0 |
| frontend-5b9c7d4f8-hq3rt | ❌ CrashLoopBackOff | 18 |
| worker-6c4d8e7b9-xk2lp | ✅ Running | 0 |
| cache-redis-0 | ✅ Running | 0 |

**Deployments**

| Deployment | Desired | Available |
|---|---|---|
| api-server | 2 | 2 ✅ |
| frontend | 1 | 0 ❌ |
| worker | 1 | 1 ✅ |

---

## Issues Found

| Severity | Resource | Issue |
|---|---|---|
| 🔴 Critical | `production/frontend` deployment | 0/1 replicas available — `ImagePullBackOff` on image `myregistry/frontend:v2.1.0` (not found in registry) |
| 🟡 Warning | `production` namespace | FailedScheduling event on worker pod — insufficient memory on nodes |

---

## Recommendations

1. **[Critical]** Fix the `frontend` image tag — `myregistry/frontend:v2.1.0` does not exist in the registry. Verify the correct tag with `docker images` or your CI pipeline and update the deployment:
   ```bash
   kubectl set image deployment/frontend frontend=myregistry/frontend:v2.0.9 -n production
   ```
2. **[Warning]** Investigate node memory pressure. Consider adding a worker node or reducing memory requests on the `worker` deployment to allow scheduling.

✅  Health check complete.
```

## Notes

- Tool output is capped at **4 000 characters** per call to stay within the model's context window on large clusters.
- The agent uses `thinking: {type: "adaptive"}` — Claude decides dynamically how much reasoning each step needs.
- Read-only: the agent only runs `kubectl get`, `kubectl describe`, and `kubectl config` commands. No cluster state is modified.
