# Agentic Kubernetes Rolling OS Upgrade

An AI-powered tool that uses Claude (`claude-opus-4-6`) to autonomously perform rolling OS upgrades across all nodes in a Kubernetes cluster. It follows the official Kubernetes drain procedure — cordon → drain → upgrade → reboot → uncordon → verify — one node at a time, with automatic drain failure recovery.

## How it works

The agent is given a set of tools (kubectl, SSH) and orchestrates the entire upgrade lifecycle autonomously:

1. **Discovers** the cluster context and all nodes
2. **Orders** upgrades — control-plane nodes first, then workers
3. **For each node** it cordons, drains, SSHes in to run the OS upgrade, reboots, waits for Ready, then uncordons
4. **Recovers from drain failures** automatically — inspects blockers, clears stuck pods, and force-drains if needed
5. **Produces a Markdown report** summarising every node's outcome

```
kubectl context → list nodes → for each node:
  cordon → drain ──[fail]──► get_drain_blockers
                             └─► delete_stuck_pods → retry drain
                                 └─► force_drain_node
                                     └─► [uncordon + stop if still fails]
  │
  ▼ (drain succeeded)
  ssh_detect_os → ssh_run_upgrade → ssh_reboot_node
  → wait_for_node_ready → uncordon → check_node_pods → ✅
```

## Requirements

- Python 3.11+
- `kubectl` configured and pointing at your cluster (`kubectl get nodes` must work)
- SSH key access to all nodes (keys in `~/.ssh`, no password prompts)
- Anthropic API key in `ANTHROPIC_API_KEY`

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Dry-run — shows every step without making any changes
python k8s_os_upgrade.py --dry-run --ssh-user ubuntu

# Live rolling upgrade of all nodes (control-plane first)
python k8s_os_upgrade.py --ssh-user ubuntu

# Upgrade a single node only
python k8s_os_upgrade.py --ssh-user ec2-user --node ip-10-0-1-42

# Common SSH usernames by cloud provider
#   Ubuntu AMI     → ubuntu
#   Amazon Linux   → ec2-user
#   RHEL           → ec2-user or root
#   GKE nodes      → (usually SSH not needed; managed)
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Print what would happen, make no changes |
| `--ssh-user USER` | `ubuntu` | SSH username for all nodes |
| `--node NODE_NAME` | all nodes | Upgrade only this specific node |

## Example output

### Dry-run mode

```
⚠️  DRY-RUN mode enabled — no upgrades or reboots will occur.

🚀  Starting K8s rolling OS upgrade agent [DRY-RUN] …

── Turn 1 ──────────────────────────────────────────────
🔧  Tool: get_current_context  args={}
Context:   arn:aws:eks:us-east-1:123456789:cluster/prod-cluster
Server:    https://ABCD1234.gr7.us-east-1.eks.amazonaws.com
User:      arn:aws:eks:us-east-1:123456789:cluster/prod-cluster
Namespace: default

── Turn 2 ──────────────────────────────────────────────
🔧  Tool: list_nodes  args={}
NAME | ROLES | STATUS | VERSION | INTERNAL-IP | OS-IMAGE
ip-10-0-1-10 | control-plane | Ready | v1.29.3 | 10.0.1.10 | Ubuntu 22.04.4 LTS
ip-10-0-2-45 | worker | Ready | v1.29.3 | 10.0.2.45 | Ubuntu 22.04.4 LTS
ip-10-0-2-67 | worker | Ready | v1.29.3 | 10.0.2.67 | Ubuntu 22.04.4 LTS

── Turn 3 ──────────────────────────────────────────────
Starting with control-plane node: ip-10-0-1-10

🔧  Tool: get_node_details  args={"node_name": "ip-10-0-1-10"}
Name: ip-10-0-1-10  ...  Status: Ready

🔧  Tool: cordon_node  args={"node_name": "ip-10-0-1-10"}
[DRY-RUN] Would cordon node: ip-10-0-1-10

🔧  Tool: drain_node  args={"node_name": "ip-10-0-1-10", "grace_period": 30}
[DRY-RUN] Would drain node: ip-10-0-1-10 (grace-period=30s)

🔧  Tool: ssh_detect_os  args={"node_ip": "10.0.1.10"}
NAME="Ubuntu"
VERSION="22.04.4 LTS (Jammy Jellyfish)"
ID=ubuntu
ID_LIKE=debian

🔧  Tool: ssh_run_upgrade  args={"node_ip": "10.0.1.10", "os_family": "debian"}
[DRY-RUN] Would run on 10.0.1.10:
  export DEBIAN_FRONTEND=noninteractive && sudo apt-get update -q && \
  sudo apt-get upgrade -y -q -o Dpkg::Options::='--force-confdef' \
  -o Dpkg::Options::='--force-confold'

🔧  Tool: ssh_reboot_node  args={"node_ip": "10.0.1.10"}
[DRY-RUN] Would reboot 10.0.1.10

🔧  Tool: wait_for_node_ready  args={"node_name": "ip-10-0-1-10", "timeout_seconds": 600}
[DRY-RUN] Would wait for ip-10-0-1-10 to become Ready.

🔧  Tool: uncordon_node  args={"node_name": "ip-10-0-1-10"}
[DRY-RUN] Would uncordon node: ip-10-0-1-10

✅ ip-10-0-1-10 — DRY-RUN complete. Would have: cordoned, drained, upgraded (apt), rebooted, uncordoned.

... (repeated for ip-10-0-2-45 and ip-10-0-2-67) ...

✅  Rolling upgrade agent finished.
```

### Live mode — successful upgrade

```
🚀  Starting K8s rolling OS upgrade agent [LIVE] …

── Turn 1 ──────────────────────────────────────────────
🔧  Tool: get_current_context  args={}
Context:   prod-cluster
Server:    https://ABCD1234.gr7.us-east-1.eks.amazonaws.com

── Turn 2 ──────────────────────────────────────────────
🔧  Tool: list_nodes  args={}
NAME | ROLES | STATUS | VERSION | INTERNAL-IP | OS-IMAGE
ip-10-0-1-10 | control-plane | Ready | v1.29.3 | 10.0.1.10 | Ubuntu 22.04.4 LTS
ip-10-0-2-45 | worker | Ready | v1.29.3 | 10.0.2.45 | Ubuntu 22.04.4 LTS

── Turn 3 ──────────────────────────────────────────────
🔧  Tool: cordon_node  args={"node_name": "ip-10-0-2-45"}
node/ip-10-0-2-45 cordoned

🔧  Tool: drain_node  args={"node_name": "ip-10-0-2-45", "grace_period": 30}
node/ip-10-0-2-45 already cordoned
evicting pod default/nginx-deployment-7d6f5c8b9-xk2p9
evicting pod monitoring/prometheus-0
pod/nginx-deployment-7d6f5c8b9-xk2p9 evicted
pod/prometheus-0 evicted
node/ip-10-0-2-45 drained

🔧  Tool: ssh_detect_os  args={"node_ip": "10.0.2.45"}
NAME="Ubuntu"
VERSION="22.04.4 LTS (Jammy Jellyfish)"

🔧  Tool: ssh_run_upgrade  args={"node_ip": "10.0.2.45", "os_family": "debian"}
  [SSH] Running upgrade on 10.0.2.45 (this may take a few minutes) …
Hit:1 http://us-east-1.ec2.archive.ubuntu.com/ubuntu jammy InRelease
Get:2 http://us-east-1.ec2.archive.ubuntu.com/ubuntu jammy-updates InRelease
...
The following packages will be upgraded:
  linux-image-5.15.0-102-generic linux-headers-5.15.0-102-generic openssh-server
3 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.

🔧  Tool: ssh_reboot_node  args={"node_ip": "10.0.2.45"}
  [SSH] Rebooting 10.0.2.45 …
Reboot command sent.

  Waiting for ip-10-0-2-45 to become Ready (timeout=600s) …
    [0s] Not ready yet () — checking again in 15s
    [15s] Not ready yet () — checking again in 15s
    [30s] Not ready yet () — checking again in 15s
    [75s] Not ready yet (False) — checking again in 15s
    [90s] Not ready yet (False) — checking again in 15s
    [105s] Not ready yet (False) — checking again in 15s
    [120s] Not ready yet (False) — checking again in 15s
    [135s] Not ready yet (False) — checking again in 15s
🔧  Tool: wait_for_node_ready  args={"node_name": "ip-10-0-2-45"}
ip-10-0-2-45 is Ready (waited ~150s)

🔧  Tool: uncordon_node  args={"node_name": "ip-10-0-2-45"}
node/ip-10-0-2-45 uncordoned

🔧  Tool: check_node_pods  args={"node_name": "ip-10-0-2-45"}
NAMESPACE    NAME                                   STATUS    NODE
default      nginx-deployment-7d6f5c8b9-m8xqz       Running   ip-10-0-2-45
monitoring   prometheus-0                            Running   ip-10-0-2-45

✅ ip-10-0-2-45 upgraded successfully.

✅  Rolling upgrade agent finished.
```

### Live mode — drain failure with automatic recovery

```
🔧  Tool: drain_node  args={"node_name": "ip-10-0-3-88", "grace_period": 30}
[ERROR exit=1]
error: unable to drain node "ip-10-0-3-88" due to error:
  cannot delete Pods declare no controller (use --force to override): default/legacy-app
  cannot delete Pods not managed by ReplicationController, ReplicaSet, Job, DaemonSet
  or StatefulSet (use --force to override): default/standalone-pod

Drain failed. Investigating blockers...

🔧  Tool: get_drain_blockers  args={"node_name": "ip-10-0-3-88"}
=== Pods on ip-10-0-3-88 ===
NAMESPACE   NAME             STATUS        NODE
default     legacy-app       Running       ip-10-0-3-88
default     standalone-pod   Terminating   ip-10-0-3-88

=== PodDisruptionBudgets (all namespaces) ===
NAMESPACE   NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
default     api-server   2               N/A               0

=== Warning events for ip-10-0-3-88 ===
(no output)

Found 1 pod stuck in Terminating. Attempting to clean up first...

🔧  Tool: delete_stuck_pods  args={"node_name": "ip-10-0-3-88"}
Force-deleted 1 stuck pod(s):
  default/standalone-pod: pod "standalone-pod" force deleted

Retrying drain after clearing stuck pods...

🔧  Tool: drain_node  args={"node_name": "ip-10-0-3-88", "grace_period": 30}
[ERROR exit=1]
error: cannot delete Pods declare no controller (use --force to override): default/legacy-app

Still failing. Escalating to force drain...

  [WARN] Force-draining ip-10-0-3-88 — overriding PDBs and eviction policies.
🔧  Tool: force_drain_node  args={"node_name": "ip-10-0-3-88"}
node/ip-10-0-3-88 already cordoned
evicting pod default/legacy-app
pod/legacy-app evicted
node/ip-10-0-3-88 drained

Force drain succeeded. Continuing with upgrade...

🔧  Tool: ssh_run_upgrade  args={"node_ip": "10.0.3.88", "os_family": "debian"}
  [SSH] Running upgrade on 10.0.3.88 (this may take a few minutes) …
...
✅ ip-10-0-3-88 upgraded (required force drain — standalone pod evicted).
```

### Final Markdown report (end of run)

```markdown
# Kubernetes Rolling OS Upgrade Report

**Cluster:** prod-cluster
**Server:** https://ABCD1234.gr7.us-east-1.eks.amazonaws.com
**Date:** 2025-06-12
**SSH User:** ubuntu

## Summary

| Node | Role | IP | OS | Duration | Result | Notes |
|------|------|----|----|----------|--------|-------|
| ip-10-0-1-10 | control-plane | 10.0.1.10 | Ubuntu 22.04.4 LTS | 4m 12s | ✅ Success | — |
| ip-10-0-2-45 | worker | 10.0.2.45 | Ubuntu 22.04.4 LTS | 3m 48s | ✅ Success | — |
| ip-10-0-3-88 | worker | 10.0.3.88 | Ubuntu 22.04.4 LTS | 5m 01s | ✅ Success | Force drain required; standalone pod evicted |

## Packages Upgraded (per node)

- `linux-image-5.15.0-102-generic`
- `linux-headers-5.15.0-102-generic`
- `openssh-server`

## Issues Encountered

- **ip-10-0-3-88**: Normal drain blocked by unmanaged pod `default/legacy-app` and a stuck
  `Terminating` pod. Recovered via `delete_stuck_pods` + `force_drain_node`. The legacy-app
  pod was evicted — **ensure it is restarted via proper deployment if needed**.

## Recommendations

- Convert `default/legacy-app` to a Deployment or StatefulSet to avoid future forced evictions.
- Review PodDisruptionBudget `default/api-server` — 0 allowed disruptions blocked eviction.
- Consider setting `minAvailable: 1` instead of `minAvailable: 2` for single-replica services.
```

## Architecture

The agent uses a manual tool-use loop against the Anthropic Messages API:

```python
while True:
    response = client.messages.create(
        model="claude-opus-4-6",
        thinking={"type": "adaptive"},   # Claude reasons step-by-step
        tools=TOOLS,
        messages=messages,
    )
    # execute tool calls, append results, loop until stop_reason == "end_turn"
```

Claude sees tool results and decides the next action. Adaptive thinking lets it reason through edge cases (drain failures, mixed OS types, NotReady nodes) without hard-coded branching logic.

## Safety notes

- The agent will **never uncordon a node before it is confirmed Ready**
- If all drain recovery attempts fail, the node is **uncordoned and restored** before stopping
- NotReady nodes are **skipped** and flagged in the report
- Nodes are upgraded **one at a time** — never in parallel
- `--dry-run` is safe to run against production clusters (zero mutations)
