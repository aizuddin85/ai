#!/usr/bin/env python3
"""
Kubernetes tool definitions and implementations for the SRE Discord bot.

All kubectl and SSH operations are here.  Two module-level settings can be
adjusted at runtime by the bot before each command:

    k8s_tools.set_dry_run(True)
    k8s_tools.set_ssh_user("ec2-user")
"""

import json
import subprocess
import time
from typing import Optional

# ── Runtime-configurable settings ─────────────────────────────────────────────

_DRY_RUN: bool = False
_SSH_USER: str = "ubuntu"


def set_dry_run(value: bool) -> None:
    global _DRY_RUN
    _DRY_RUN = value


def set_ssh_user(value: str) -> None:
    global _SSH_USER
    _SSH_USER = value


def get_dry_run() -> bool:
    return _DRY_RUN


def get_ssh_user() -> str:
    return _SSH_USER


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _kubectl(args: list[str], timeout: int = 120) -> str:
    """Run a kubectl command and return combined stdout/stderr."""
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip()
            return f"[ERROR exit={result.returncode}]\n{err}"
        return out if out else "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] kubectl timed out after {timeout}s"
    except FileNotFoundError:
        return "[ERROR] kubectl not found on PATH"


def _ssh(node_ip: str, command: str, timeout: int = 120) -> str:
    """Run a command on a remote node via SSH (key auth only)."""
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
        f"{_SSH_USER}@{node_ip}",
        command,
    ]
    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0 and not out:
            return f"[ERROR exit={result.returncode}]\n{err}"
        return (out + (f"\n[stderr]\n{err}" if err else "")) or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] SSH timed out after {timeout}s"
    except FileNotFoundError:
        return "[ERROR] ssh not found on PATH"


# ── Tool implementations ───────────────────────────────────────────────────────

def get_current_context(_: dict) -> str:
    context = _kubectl(["config", "current-context"])
    server   = _kubectl(["config", "view", "--minify",
                          "-o", "jsonpath={.clusters[0].cluster.server}"])
    user     = _kubectl(["config", "view", "--minify",
                          "-o", "jsonpath={.users[0].name}"])
    ns       = _kubectl(["config", "view", "--minify",
                          "-o", "jsonpath={.contexts[0].context.namespace}"])
    return (
        f"Context:   {context}\n"
        f"Server:    {server}\n"
        f"User:      {user}\n"
        f"Namespace: {ns or 'default'}"
    )


def list_namespaces(_: dict) -> str:
    return _kubectl([
        "get", "namespaces", "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name,STATUS:.status.phase,AGE:.metadata.creationTimestamp",
    ])


def list_nodes(_: dict) -> str:
    raw = _kubectl(["get", "nodes", "-o", "json"], timeout=30)
    if raw.startswith("[ERROR"):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    lines = ["NAME | ROLES | STATUS | VERSION | INTERNAL-IP | OS-IMAGE"]
    for item in data.get("items", []):
        name   = item["metadata"]["name"]
        labels = item["metadata"].get("labels", {})
        roles  = ",".join(
            k.replace("node-role.kubernetes.io/", "")
            for k in labels if k.startswith("node-role.kubernetes.io/")
        ) or "worker"
        version = item["status"].get("nodeInfo", {}).get("kubeletVersion", "?")
        os_img  = item["status"].get("nodeInfo", {}).get("osImage", "?")
        conditions = item["status"].get("conditions", [])
        ready = next((c["status"] for c in conditions if c["type"] == "Ready"), "Unknown")
        status_str = "Ready" if ready == "True" else f"NotReady({ready})"
        addrs  = item["status"].get("addresses", [])
        ip     = next((a["address"] for a in addrs if a["type"] == "InternalIP"), "?")
        lines.append(f"{name} | {roles} | {status_str} | {version} | {ip} | {os_img}")
    return "\n".join(lines)


def get_node_details(args: dict) -> str:
    return _kubectl(["describe", "node", args["node_name"]], timeout=30)


def check_pods(args: dict) -> str:
    ns = args["namespace"]
    return _kubectl([
        "get", "pods", "-n", ns,
        "-o", "wide", "--no-headers",
        "--sort-by=.status.phase",
    ])


def check_deployments(args: dict) -> str:
    ns = args["namespace"]
    return _kubectl([
        "get", "deployments", "-n", ns,
        "-o", "custom-columns="
        "NAME:.metadata.name,"
        "DESIRED:.spec.replicas,"
        "AVAILABLE:.status.availableReplicas,"
        "READY:.status.readyReplicas,"
        "AGE:.metadata.creationTimestamp",
    ])


def check_services(args: dict) -> str:
    return _kubectl(["get", "services", "-n", args["namespace"]])


def check_events(args: dict) -> str:
    return _kubectl([
        "get", "events", "-n", args["namespace"],
        "--field-selector=type=Warning",
        "--sort-by=.lastTimestamp",
    ])


def check_nodes(_: dict) -> str:
    return _kubectl([
        "get", "nodes",
        "-o", "custom-columns="
        "NAME:.metadata.name,"
        "STATUS:.status.conditions[-1].type,"
        "ROLES:.metadata.labels.kubernetes\\.io/role,"
        "AGE:.metadata.creationTimestamp,"
        "VERSION:.status.nodeInfo.kubeletVersion",
    ])


def describe_resource(args: dict) -> str:
    cmd = ["describe", args["resource_type"], args["resource_name"]]
    if args.get("namespace"):
        cmd += ["-n", args["namespace"]]
    return _kubectl(cmd)


def check_node_pods(args: dict) -> str:
    return _kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector", f"spec.nodeName={args['node_name']}",
        "-o", "wide",
    ], timeout=30)


def kubectl_run(args: dict) -> str:
    """Run an arbitrary kubectl command (provided as a list of args)."""
    raw_args = args.get("args", [])
    if isinstance(raw_args, str):
        import shlex
        raw_args = shlex.split(raw_args)
    # Safety: block destructive verbs unless the caller explicitly allows them
    dangerous = {"delete", "patch", "edit", "apply", "replace", "create", "exec"}
    if raw_args and raw_args[0].lower() in dangerous and not args.get("allow_write"):
        return (
            f"[BLOCKED] '{raw_args[0]}' is a write operation. "
            "Set allow_write=true to permit it."
        )
    return _kubectl(raw_args, timeout=args.get("timeout", 60))


# ── Node lifecycle tools ───────────────────────────────────────────────────────

def cordon_node(args: dict) -> str:
    node = args["node_name"]
    if _DRY_RUN:
        return f"[DRY-RUN] Would cordon node: {node}"
    return _kubectl(["cordon", node])


def drain_node(args: dict) -> str:
    node         = args["node_name"]
    grace_period = args.get("grace_period", 30)
    if _DRY_RUN:
        return f"[DRY-RUN] Would drain node: {node} (grace-period={grace_period}s)"
    return _kubectl([
        "drain", node,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        f"--grace-period={grace_period}",
        "--timeout=300s",
    ], timeout=360)


def force_drain_node(args: dict) -> str:
    node         = args["node_name"]
    grace_period = args.get("grace_period", 0)
    if _DRY_RUN:
        return f"[DRY-RUN] Would force-drain node: {node}"
    return _kubectl([
        "drain", node,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        "--force",
        "--disable-eviction",
        f"--grace-period={grace_period}",
        "--timeout=300s",
    ], timeout=360)


def uncordon_node(args: dict) -> str:
    node = args["node_name"]
    if _DRY_RUN:
        return f"[DRY-RUN] Would uncordon node: {node}"
    return _kubectl(["uncordon", node])


def wait_for_node_ready(args: dict) -> str:
    node    = args["node_name"]
    timeout = int(args.get("timeout_seconds", 600))
    interval = 15
    elapsed  = 0

    if _DRY_RUN:
        return f"[DRY-RUN] Would wait for {node} to become Ready."

    while elapsed < timeout:
        raw = _kubectl([
            "get", "node", node,
            "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
        ], timeout=15)
        if raw.strip() == "True":
            return f"{node} is Ready (waited ~{elapsed}s)"
        time.sleep(interval)
        elapsed += interval
    return f"[TIMEOUT] {node} did not become Ready within {timeout}s"


def delete_stuck_pods(args: dict) -> str:
    node = args["node_name"]
    if _DRY_RUN:
        return f"[DRY-RUN] Would force-delete stuck Terminating pods on {node}"

    raw = _kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector", f"spec.nodeName={node}",
        "-o", "json",
    ], timeout=30)
    if raw.startswith("[ERROR"):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    deleted, skipped = [], []
    for pod in data.get("items", []):
        phase       = pod.get("status", {}).get("phase", "")
        deletion_ts = pod["metadata"].get("deletionTimestamp")
        ns   = pod["metadata"]["namespace"]
        name = pod["metadata"]["name"]
        if deletion_ts or phase == "Terminating":
            result = _kubectl(
                ["delete", "pod", name, "-n", ns, "--grace-period=0", "--force"],
                timeout=30,
            )
            deleted.append(f"{ns}/{name}: {result}")
        else:
            skipped.append(f"{ns}/{name} ({phase})")

    lines = []
    if deleted:
        lines.append(f"Force-deleted {len(deleted)} stuck pod(s):")
        lines.extend(f"  {d}" for d in deleted)
    if skipped:
        lines.append(f"Skipped {len(skipped)} non-Terminating pod(s).")
    return "\n".join(lines) or "No pods found on node."


def get_drain_blockers(args: dict) -> str:
    node = args["node_name"]
    pods_raw   = _kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector", f"spec.nodeName={node}", "-o", "wide",
    ], timeout=30)
    pdb_raw    = _kubectl(["get", "poddisruptionbudgets", "--all-namespaces", "-o", "wide"], timeout=30)
    events_raw = _kubectl([
        "get", "events", "--all-namespaces",
        f"--field-selector=involvedObject.name={node},type=Warning",
        "--sort-by=.lastTimestamp",
    ], timeout=30)
    return (
        f"=== Pods on {node} ===\n{pods_raw}\n\n"
        f"=== PodDisruptionBudgets ===\n{pdb_raw}\n\n"
        f"=== Warning events ===\n{events_raw}"
    )


# ── SSH / OS tools ─────────────────────────────────────────────────────────────

def ssh_detect_os(args: dict) -> str:
    ip  = args["node_ip"]
    cmd = "cat /etc/os-release 2>/dev/null || cat /etc/redhat-release 2>/dev/null"
    return _ssh(ip, cmd, timeout=20)


def ssh_check_updates(args: dict) -> str:
    ip        = args["node_ip"]
    os_family = args["os_family"]

    if os_family == "debian":
        check_cmd = (
            "sudo apt-get update -qq 2>/dev/null; "
            "UPDATES=$(apt-get --just-print upgrade 2>/dev/null | grep -c '^Inst ' || echo 0); "
            "if [ -f /var/run/reboot-required ]; then REBOOT=true; else REBOOT=false; fi; "
            "if [ \"$UPDATES\" -gt 0 ]; then UP=true; else UP=false; fi; "
            "echo \"updates_available: $UP\"; echo \"pending_count: $UPDATES\"; echo \"reboot_required: $REBOOT\""
        )
    else:
        check_cmd = (
            "if yum check-update -q 2>/dev/null; then YE=0; else YE=$?; fi; "
            "if [ $YE -eq 100 ]; then UP=true; COUNT=$(yum check-update -q 2>/dev/null | grep -c '^[[:alpha:]]' || echo 0); "
            "elif [ $YE -eq 0 ]; then UP=false; COUNT=0; "
            "else "
            "  if dnf check-update -q 2>/dev/null; then DE=0; else DE=$?; fi; "
            "  if [ $DE -eq 100 ]; then UP=true; COUNT=$(dnf check-update -q 2>/dev/null | grep -c '^[[:alpha:]]' || echo 0); "
            "  else UP=false; COUNT=0; fi; "
            "fi; "
            "if needs-restarting -r 2>/dev/null; then REBOOT=false; else REBOOT=true; fi; "
            "echo \"updates_available: $UP\"; echo \"pending_count: $COUNT\"; echo \"reboot_required: $REBOOT\""
        )
    return _ssh(ip, check_cmd, timeout=60)


def ssh_run_upgrade(args: dict) -> str:
    ip        = args["node_ip"]
    os_family = args["os_family"]

    if os_family == "debian":
        upgrade_cmd = (
            "export DEBIAN_FRONTEND=noninteractive && "
            "sudo apt-get update -q && "
            "sudo apt-get upgrade -y -q "
            "-o Dpkg::Options::='--force-confdef' "
            "-o Dpkg::Options::='--force-confold'"
        )
    else:
        upgrade_cmd = "sudo yum update -y 2>/dev/null || sudo dnf update -y"

    if _DRY_RUN:
        return f"[DRY-RUN] Would run on {ip}:\n  {upgrade_cmd}"
    return _ssh(ip, upgrade_cmd, timeout=600)


def ssh_reboot_node(args: dict) -> str:
    ip = args["node_ip"]
    if _DRY_RUN:
        return f"[DRY-RUN] Would reboot {ip}"
    output = _ssh(ip, "sudo shutdown -r +0 'OS upgrade reboot' &", timeout=15)
    time.sleep(3)
    return output or "Reboot command sent."


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "get_current_context",
        "description": "Get the active kubectl context, cluster server URL, user, and namespace.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_namespaces",
        "description": "List all namespaces in the cluster with their status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_nodes",
        "description": "List all nodes with status, roles, Kubernetes version, and internal IP.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_node_details",
        "description": "Return full details for a single node (kubectl describe node).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Exact Kubernetes node name."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "check_pods",
        "description": "Get the status of all pods in a namespace (name, status, ready, restarts, age).",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace to inspect."},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_deployments",
        "description": "Get the status of all deployments in a namespace (desired/available/ready replicas).",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace."},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_services",
        "description": "List all services in a namespace with type, cluster IP, and ports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace."},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_events",
        "description": "Fetch recent Warning events in a namespace — useful for diagnosing issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace."},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_nodes",
        "description": "Get status and version of all cluster nodes.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_resource",
        "description": (
            "Run 'kubectl describe' on a specific resource for deep inspection. "
            "Use when a resource looks unhealthy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "description": "e.g. pod, deployment, node, service."},
                "resource_name": {"type": "string", "description": "Name of the resource."},
                "namespace": {"type": "string", "description": "Namespace (omit for cluster-scoped resources)."},
            },
            "required": ["resource_type", "resource_name"],
        },
    },
    {
        "name": "check_node_pods",
        "description": "List all pods scheduled on a specific node across all namespaces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node name."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "kubectl_run",
        "description": (
            "Run an arbitrary kubectl command. "
            "Pass args as a list, e.g. ['get', 'pods', '-n', 'kube-system']. "
            "Write operations (delete, apply, patch, etc.) are blocked unless allow_write=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "kubectl arguments (excluding 'kubectl' itself).",
                },
                "allow_write": {
                    "type": "boolean",
                    "description": "Set true to permit write operations. Default false.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60).",
                },
            },
            "required": ["args"],
        },
    },
    # ── Node lifecycle ──────────────────────────────────────────────────────────
    {
        "name": "cordon_node",
        "description": "Mark a node as unschedulable (kubectl cordon). Always do this before draining.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node to cordon."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "drain_node",
        "description": (
            "Safely evict all pods from a node (kubectl drain). "
            "Ignores DaemonSets and deletes emptyDir data. Cordon first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
                "grace_period": {"type": "integer", "description": "Termination grace period in seconds (default 30)."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "force_drain_node",
        "description": (
            "Force-drain a node with --force --disable-eviction. "
            "Use only after drain_node fails due to PDBs or stuck pods."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
                "grace_period": {"type": "integer", "description": "Grace period in seconds (default 0)."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "uncordon_node",
        "description": "Re-enable scheduling on a node (kubectl uncordon). Only after node is Ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "wait_for_node_ready",
        "description": "Poll until the node reports Ready. Waits up to timeout_seconds (default 600).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
                "timeout_seconds": {"type": "integer", "description": "Max wait time (default 600)."},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "delete_stuck_pods",
        "description": "Force-delete pods stuck in Terminating state on a node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "get_drain_blockers",
        "description": (
            "Inspect why a drain is failing — returns pods on node, PDBs, and warning events. "
            "Call after drain_node fails."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
            },
            "required": ["node_name"],
        },
    },
    # ── SSH / OS tools ──────────────────────────────────────────────────────────
    {
        "name": "ssh_detect_os",
        "description": "SSH into a node and detect its OS (debian/ubuntu vs rhel/centos/amazon).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string", "description": "IP address of the node."},
            },
            "required": ["node_ip"],
        },
    },
    {
        "name": "ssh_check_updates",
        "description": (
            "SSH into a node and check for pending OS updates and reboot requirement. "
            "Returns updates_available and reboot_required flags. Call BEFORE cordoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string"},
                "os_family": {
                    "type": "string",
                    "enum": ["debian", "rhel"],
                    "description": "'debian' for Ubuntu/Debian, 'rhel' for RHEL/CentOS/Amazon Linux.",
                },
            },
            "required": ["node_ip", "os_family"],
        },
    },
    {
        "name": "ssh_run_upgrade",
        "description": (
            "SSH into a node and run the OS package upgrade (apt-get or yum/dnf). "
            "In dry-run mode logs commands without executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string"},
                "os_family": {"type": "string", "enum": ["debian", "rhel"]},
            },
            "required": ["node_ip", "os_family"],
        },
    },
    {
        "name": "ssh_reboot_node",
        "description": "SSH into a node and issue a reboot. Follow with wait_for_node_ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string"},
            },
            "required": ["node_ip"],
        },
    },
]

TOOL_MAP: dict = {
    "get_current_context": get_current_context,
    "list_namespaces":     list_namespaces,
    "list_nodes":          list_nodes,
    "get_node_details":    get_node_details,
    "check_pods":          check_pods,
    "check_deployments":   check_deployments,
    "check_services":      check_services,
    "check_events":        check_events,
    "check_nodes":         check_nodes,
    "describe_resource":   describe_resource,
    "check_node_pods":     check_node_pods,
    "kubectl_run":         kubectl_run,
    "cordon_node":         cordon_node,
    "drain_node":          drain_node,
    "force_drain_node":    force_drain_node,
    "uncordon_node":       uncordon_node,
    "wait_for_node_ready": wait_for_node_ready,
    "delete_stuck_pods":   delete_stuck_pods,
    "get_drain_blockers":  get_drain_blockers,
    "ssh_detect_os":       ssh_detect_os,
    "ssh_check_updates":   ssh_check_updates,
    "ssh_run_upgrade":     ssh_run_upgrade,
    "ssh_reboot_node":     ssh_reboot_node,
}
