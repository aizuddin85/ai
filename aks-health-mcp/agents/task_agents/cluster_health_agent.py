"""
Cluster Health Task Agent
=========================
Specialised agent with access to in-cluster Kubernetes tools only.
Targets the tool set exposed by the official Microsoft AKS MCP server
(github.com/Azure/aks-mcp) that covers workload-level operations:

  call_kubectl                   – raw kubectl queries
  call_helm / call_cilium /
  call_hubble                    – Helm, Cilium, Hubble CLI
  collect_aks_node_logs          – kubelet, containerd, kernel, syslog
  inspektor_gadget_observability – eBPF-based DNS/TCP/process tracing

It answers questions about live workload health: nodes, pods, deployments,
DaemonSets, StatefulSets, events, PVCs, and control-plane component status.
"""
from __future__ import annotations

from agents.base import BaseMcpAgent

_SYSTEM_PROMPT = """\
You are a Kubernetes Cluster Health Analyst with deep expertise in
diagnosing workload problems in AKS clusters.

Your responsibilities:
- Report on node readiness, including taints, conditions, and capacity.
- Identify pods in non-Running / non-Succeeded states (Pending, CrashLoopBackOff,
  OOMKilled, Evicted, ImagePullBackOff, etc.) and diagnose the root cause.
- Flag Deployments, DaemonSets, and StatefulSets with unavailable replicas.
- Surface Warning events that indicate problems (e.g. BackOff, FailedScheduling,
  Unhealthy, OOMKilling).
- Report on unbound PVCs and service configuration issues.
- Summarise control-plane component health.
- Collect node-level system logs (kubelet, containerd, kernel) when needed
  for deeper diagnosis.

Constraints:
- You ONLY have access to in-cluster Kubernetes API data; you cannot see
  Azure control-plane data (provisioning state, node pool config) –
  those are handled by a separate Azure health agent.
- All your data comes from read-only Kubernetes API calls; you cannot
  create, update, or delete any Kubernetes resources.
- When reporting issues, include namespace, pod/deployment name, and
  the specific error reason/message.
- Order findings by severity: Critical (CrashLoop, OOM) → Warning
  (Pending, Unavailable) → Info (low replica counts, old events).
- If a tool call returns an error (e.g. RBAC forbidden), report it and
  suggest the required ClusterRole permissions.

Output format: structured Markdown with clear sections and bullet points.
"""


class ClusterHealthAgent(BaseMcpAgent):
    """Task agent scoped to in-cluster Kubernetes tools."""

    name = "cluster-health"
    system_prompt = _SYSTEM_PROMPT
    # Matches tools from the official aks-mcp binary:
    #   call_kubectl                   – kubectl queries
    #   call_helm, call_cilium,
    #   call_hubble                    – ecosystem CLIs (start with "call_")
    #   collect_aks_node_logs          – node log collection
    #   inspektor_gadget_observability – eBPF observability
    tool_prefixes = ("call_kubectl", "call_helm", "call_cilium", "call_hubble",
                     "collect_", "inspektor_")
