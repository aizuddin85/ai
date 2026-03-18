#!/usr/bin/env python3
"""
Agentic Kubernetes Node OS Upgrade
Uses Claude with tool use to autonomously perform rolling OS upgrades
across all nodes: cordon → drain → SSH upgrade → reboot → uncordon → verify.

Usage:
    python k8s_os_upgrade.py [--dry-run] [--ssh-user USER] [--node NODE]

    --dry-run           Print what would be done without executing upgrades or reboots.
    --ssh-user USER     SSH username for nodes (default: ubuntu).
    --node NODE         Upgrade only this specific node (by name).
"""

import argparse
import json
import subprocess
import sys
import time

import anthropic

# ── Globals set by CLI ────────────────────────────────────────────────────────

DRY_RUN: bool = False
SSH_USER: str = "ubuntu"
TARGET_NODE: str | None = None  # None = all nodes


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_current_context",
        "description": (
            "Return the active kubectl context, cluster server URL, user, and namespace."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_nodes",
        "description": (
            "List all nodes in the cluster with their status, roles, Kubernetes version, "
            "and internal IP address. Use this to decide the upgrade order."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_node_details",
        "description": (
            "Return full details for a single node including its internal IP, OS image, "
            "kernel version, container runtime, and current conditions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {
                    "type": "string",
                    "description": "Exact Kubernetes node name.",
                }
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "cordon_node",
        "description": (
            "Mark a node as unschedulable (kubectl cordon). "
            "Always do this before draining."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node to cordon."}
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "drain_node",
        "description": (
            "Safely evict all pods from a node (kubectl drain). "
            "Ignores DaemonSets and deletes emissary data. "
            "Always cordon the node first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node to drain."},
                "grace_period": {
                    "type": "integer",
                    "description": "Pod termination grace period in seconds (default 30).",
                },
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "ssh_detect_os",
        "description": (
            "SSH into a node and detect its OS type (debian/ubuntu vs rhel/centos/amzn) "
            "and current package manager. Returns OS details needed to choose upgrade commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {
                    "type": "string",
                    "description": "IP address or hostname of the node.",
                }
            },
            "required": ["node_ip"],
        },
    },
    {
        "name": "ssh_check_updates",
        "description": (
            "SSH into a node and check whether any OS packages have pending updates "
            "and whether a reboot is required. "
            "Returns 'updates_available: true/false' and 'reboot_required: true/false'. "
            "Call this BEFORE cordoning/draining — if no updates and no reboot needed, "
            "skip the entire drain/upgrade/reboot cycle for this node."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string", "description": "IP of the node to inspect."},
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
            "SSH into a node and run the OS package upgrade. "
            "Automatically selects apt-get or yum/dnf based on OS detection. "
            "Returns command output. In dry-run mode prints commands without executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string", "description": "IP of the node to upgrade."},
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
        "name": "ssh_reboot_node",
        "description": (
            "SSH into a node and issue a reboot. "
            "In dry-run mode this is skipped. "
            "After calling this, use wait_for_node_ready to poll until the node is back."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ip": {"type": "string", "description": "IP of the node to reboot."}
            },
            "required": ["node_ip"],
        },
    },
    {
        "name": "wait_for_node_ready",
        "description": (
            "Poll kubectl until the node reports Ready status. "
            "Waits up to 10 minutes, checking every 15 seconds. "
            "Returns 'Ready' or a timeout/error message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node name to watch."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": "How long to wait before giving up (default 600).",
                },
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "uncordon_node",
        "description": (
            "Re-enable scheduling on a node (kubectl uncordon). "
            "Only call this after the node is Ready."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node to uncordon."}
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "check_node_pods",
        "description": (
            "List pods currently scheduled on a specific node. "
            "Useful for verifying drain succeeded and workloads returned after uncordon."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node name to inspect."}
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "force_drain_node",
        "description": (
            "Forcefully drain a node using --force --disable-eviction flags. "
            "Use this as a second attempt when drain_node fails due to PodDisruptionBudgets "
            "or pods that refuse graceful eviction. Logs a warning before proceeding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node to force-drain."},
                "grace_period": {
                    "type": "integer",
                    "description": "Pod termination grace period in seconds (default 0 for force).",
                },
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "delete_stuck_pods",
        "description": (
            "Force-delete pods that are stuck in Terminating state on a node. "
            "Use this when drain_node or force_drain_node leaves pods in Terminating "
            "status that block the drain. Returns the list of pods deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {
                    "type": "string",
                    "description": "Node whose stuck Terminating pods should be force-deleted.",
                }
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "get_drain_blockers",
        "description": (
            "Inspect why a drain is failing. Returns pods on the node that are not DaemonSets, "
            "PodDisruptionBudgets that may be blocking eviction, and any recent Warning events. "
            "Call this after a drain_node failure to understand what needs fixing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "Node that failed to drain."}
            },
            "required": ["node_name"],
        },
    },
]


# ── Helper: run kubectl ────────────────────────────────────────────────────────

def _kubectl(args: list[str], timeout: int = 120) -> str:
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


# ── Helper: run SSH command ────────────────────────────────────────────────────

def _ssh(node_ip: str, command: str, timeout: int = 120) -> str:
    """Run a command on a remote node via SSH using keys from ~/.ssh."""
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",          # never prompt for password
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
        f"{SSH_USER}@{node_ip}",
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
        combined = out
        if err:
            combined += f"\n[stderr]\n{err}"
        return combined if combined else "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] SSH command timed out after {timeout}s"
    except FileNotFoundError:
        return "[ERROR] ssh not found on PATH"


# ── Tool implementations ───────────────────────────────────────────────────────

def get_current_context(_: dict) -> str:
    context = _kubectl(["config", "current-context"])
    server  = _kubectl(["config", "view", "--minify",
                         "-o", "jsonpath={.clusters[0].cluster.server}"])
    user    = _kubectl(["config", "view", "--minify",
                         "-o", "jsonpath={.users[0].name}"])
    ns      = _kubectl(["config", "view", "--minify",
                         "-o", "jsonpath={.contexts[0].context.namespace}"])
    return (
        f"Context:   {context}\n"
        f"Server:    {server}\n"
        f"User:      {user}\n"
        f"Namespace: {ns or 'default'}"
    )


def list_nodes(_: dict) -> str:
    # Get node name, status, roles, version, internal IP
    raw = _kubectl([
        "get", "nodes",
        "-o", "json",
    ], timeout=30)
    if raw.startswith("[ERROR"):
        return raw

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # fallback to raw text

    lines = ["NAME | ROLES | STATUS | VERSION | INTERNAL-IP | OS-IMAGE"]
    for item in data.get("items", []):
        name    = item["metadata"]["name"]
        labels  = item["metadata"].get("labels", {})
        roles   = ",".join(
            k.replace("node-role.kubernetes.io/", "")
            for k in labels if k.startswith("node-role.kubernetes.io/")
        ) or "worker"
        version = item["status"].get("nodeInfo", {}).get("kubeletVersion", "?")
        os_img  = item["status"].get("nodeInfo", {}).get("osImage", "?")
        # Ready condition
        conditions = item["status"].get("conditions", [])
        ready = next(
            (c["status"] for c in conditions if c["type"] == "Ready"), "Unknown"
        )
        status_str = "Ready" if ready == "True" else f"NotReady({ready})"
        # Internal IP
        addrs = item["status"].get("addresses", [])
        ip = next((a["address"] for a in addrs if a["type"] == "InternalIP"), "?")
        lines.append(f"{name} | {roles} | {status_str} | {version} | {ip} | {os_img}")

    return "\n".join(lines)


def get_node_details(args: dict) -> str:
    node = args["node_name"]
    return _kubectl(["describe", "node", node], timeout=30)


def cordon_node(args: dict) -> str:
    node = args["node_name"]
    if DRY_RUN:
        return f"[DRY-RUN] Would cordon node: {node}"
    return _kubectl(["cordon", node])


def drain_node(args: dict) -> str:
    node         = args["node_name"]
    grace_period = args.get("grace_period", 30)
    if DRY_RUN:
        return f"[DRY-RUN] Would drain node: {node} (grace-period={grace_period}s)"
    return _kubectl([
        "drain", node,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        f"--grace-period={grace_period}",
        "--timeout=300s",
    ], timeout=360)


def ssh_detect_os(args: dict) -> str:
    ip = args["node_ip"]
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
            "echo \"updates_available: $UP\"; "
            "echo \"pending_count: $UPDATES\"; "
            "echo \"reboot_required: $REBOOT\""
        )
    else:  # rhel / centos / amazon linux
        # yum/dnf check-update exits 100 when updates are available, 0 when none, 1 on error.
        # needs-restarting -r exits 1 if reboot needed, 0 if not — never capture its stdout.
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
            "echo \"updates_available: $UP\"; "
            "echo \"pending_count: $COUNT\"; "
            "echo \"reboot_required: $REBOOT\""
        )

    return _ssh(ip, check_cmd, timeout=60)


def ssh_run_upgrade(args: dict) -> str:
    ip         = args["node_ip"]
    os_family  = args["os_family"]

    if os_family == "debian":
        upgrade_cmd = (
            "export DEBIAN_FRONTEND=noninteractive && "
            "sudo apt-get update -q && "
            "sudo apt-get upgrade -y -q "
            "-o Dpkg::Options::='--force-confdef' "
            "-o Dpkg::Options::='--force-confold'"
        )
    else:  # rhel / centos / amazon linux
        upgrade_cmd = "sudo yum update -y 2>/dev/null || sudo dnf update -y"

    if DRY_RUN:
        return f"[DRY-RUN] Would run on {ip}:\n  {upgrade_cmd}"

    print(f"  [SSH] Running upgrade on {ip} (this may take a few minutes) …")
    return _ssh(ip, upgrade_cmd, timeout=600)


def ssh_reboot_node(args: dict) -> str:
    ip = args["node_ip"]
    if DRY_RUN:
        return f"[DRY-RUN] Would reboot {ip}"
    print(f"  [SSH] Rebooting {ip} …")
    # Reboot in background so SSH exits cleanly
    output = _ssh(ip, "sudo shutdown -r +0 'OS upgrade reboot' &", timeout=15)
    time.sleep(3)   # small buffer so node has time to initiate shutdown
    return output or "Reboot command sent."


def wait_for_node_ready(args: dict) -> str:
    node            = args["node_name"]
    timeout_seconds = int(args.get("timeout_seconds", 600))
    interval        = 15
    elapsed         = 0

    if DRY_RUN:
        return f"[DRY-RUN] Would wait for {node} to become Ready."

    print(f"  Waiting for {node} to become Ready (timeout={timeout_seconds}s) …")
    while elapsed < timeout_seconds:
        raw = _kubectl([
            "get", "node", node,
            "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
        ], timeout=15)
        if raw.strip() == "True":
            return f"{node} is Ready (waited ~{elapsed}s)"
        print(f"    [{elapsed}s] Not ready yet ({raw.strip()}) — checking again in {interval}s")
        time.sleep(interval)
        elapsed += interval

    return f"[TIMEOUT] {node} did not become Ready within {timeout_seconds}s"


def uncordon_node(args: dict) -> str:
    node = args["node_name"]
    if DRY_RUN:
        return f"[DRY-RUN] Would uncordon node: {node}"
    return _kubectl(["uncordon", node])


def check_node_pods(args: dict) -> str:
    node = args["node_name"]
    return _kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector", f"spec.nodeName={node}",
        "-o", "wide",
    ], timeout=30)


def force_drain_node(args: dict) -> str:
    node         = args["node_name"]
    grace_period = args.get("grace_period", 0)
    if DRY_RUN:
        return f"[DRY-RUN] Would force-drain node: {node} (grace-period={grace_period}s)"
    print(f"  [WARN] Force-draining {node} — overriding PDBs and eviction policies.")
    return _kubectl([
        "drain", node,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        "--force",
        "--disable-eviction",
        f"--grace-period={grace_period}",
        "--timeout=300s",
    ], timeout=360)


def delete_stuck_pods(args: dict) -> str:
    node = args["node_name"]
    if DRY_RUN:
        return f"[DRY-RUN] Would force-delete stuck Terminating pods on {node}"

    # Find pods in Terminating state on the node
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

    deleted = []
    skipped = []
    for pod in data.get("items", []):
        phase = pod.get("status", {}).get("phase", "")
        deletion_ts = pod["metadata"].get("deletionTimestamp")
        if deletion_ts or phase == "Terminating":
            ns   = pod["metadata"]["namespace"]
            name = pod["metadata"]["name"]
            result = _kubectl([
                "delete", "pod", name,
                "-n", ns,
                "--grace-period=0",
                "--force",
            ], timeout=30)
            deleted.append(f"{ns}/{name}: {result}")
        else:
            skipped.append(f"{pod['metadata']['namespace']}/{pod['metadata']['name']} ({phase})")

    if not deleted and not skipped:
        return "No pods found on node."
    lines = []
    if deleted:
        lines.append(f"Force-deleted {len(deleted)} stuck pod(s):")
        lines.extend(f"  {d}" for d in deleted)
    if skipped:
        lines.append(f"Skipped {len(skipped)} non-Terminating pod(s):")
        lines.extend(f"  {s}" for s in skipped)
    return "\n".join(lines)


def get_drain_blockers(args: dict) -> str:
    node = args["node_name"]
    sections = []

    # Non-DaemonSet pods still on the node
    pods_raw = _kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector", f"spec.nodeName={node}",
        "-o", "wide",
    ], timeout=30)
    sections.append(f"=== Pods on {node} ===\n{pods_raw}")

    # PodDisruptionBudgets across all namespaces
    pdb_raw = _kubectl([
        "get", "poddisruptionbudgets", "--all-namespaces",
        "-o", "wide",
    ], timeout=30)
    sections.append(f"=== PodDisruptionBudgets (all namespaces) ===\n{pdb_raw}")

    # Recent Warning events on the node
    events_raw = _kubectl([
        "get", "events", "--all-namespaces",
        f"--field-selector=involvedObject.name={node},type=Warning",
        "--sort-by=.lastTimestamp",
    ], timeout=30)
    sections.append(f"=== Warning events for {node} ===\n{events_raw}")

    return "\n\n".join(sections)


TOOL_MAP = {
    "get_current_context":  get_current_context,
    "list_nodes":           list_nodes,
    "get_node_details":     get_node_details,
    "cordon_node":          cordon_node,
    "drain_node":           drain_node,
    "force_drain_node":     force_drain_node,
    "delete_stuck_pods":    delete_stuck_pods,
    "get_drain_blockers":   get_drain_blockers,
    "ssh_detect_os":        ssh_detect_os,
    "ssh_check_updates":    ssh_check_updates,
    "ssh_run_upgrade":      ssh_run_upgrade,
    "ssh_reboot_node":      ssh_reboot_node,
    "wait_for_node_ready":  wait_for_node_ready,
    "uncordon_node":        uncordon_node,
    "check_node_pods":      check_node_pods,
}


# ── System prompt ──────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    node_scope = (
        f"Only upgrade the node named '{TARGET_NODE}'."
        if TARGET_NODE
        else "Upgrade ALL nodes in rolling order: control-plane nodes first, then worker nodes."
    )
    dry_run_note = (
        "\n⚠️  DRY-RUN MODE IS ACTIVE. No actual upgrades, reboots, or drain operations "
        "will be executed. Just log what would happen.\n"
        if DRY_RUN
        else ""
    )
    return f"""\
You are an expert Kubernetes Site Reliability Engineer performing a rolling OS upgrade.
{dry_run_note}
Your SSH username is '{SSH_USER}'. SSH keys are pre-loaded from ~/.ssh — no password needed.

SCOPE: {node_scope}

ROLLING UPGRADE PROCEDURE (repeat for each node, one at a time):
1. get_current_context      — confirm the cluster you are about to modify.
2. list_nodes               — get all nodes, their IPs, roles, and current status.
3. For each node (in order):
   a. get_node_details      — check node is currently Ready before touching it.
      Skip to next node if NotReady.
   b. ssh_detect_os         — detect Debian/Ubuntu vs RHEL/CentOS/Amazon Linux.
   c. ssh_check_updates     — check whether packages need upgrading and/or a reboot is pending.
      ► If updates_available=false AND reboot_required=false:
            Log "⏭️  <node_name> — already up-to-date, no reboot needed. Skipping."
            Do NOT cordon, drain, upgrade, or reboot. Move to the next node.
      ► Otherwise continue with steps d–j.
   d. cordon_node           — mark as unschedulable so no new pods land on it.
   e. drain_node            — evict existing pods (ignores DaemonSets).
      → If drain fails, follow the DRAIN RECOVERY PROCEDURE below before continuing.
   f. ssh_run_upgrade       — run the appropriate package upgrade (skip if updates_available=false).
   g. ssh_reboot_node       — reboot to apply changes (skip if reboot_required=false AND no upgrade was run).
   h. wait_for_node_ready   — wait until kubectl shows the node is Ready again.
   i. uncordon_node         — re-enable scheduling.
   j. check_node_pods       — verify workloads are rescheduled.
   k. Log a ✅ summary for this node before moving to the next.
4. After all nodes are done, write a final Markdown upgrade report with:
   - Cluster context
   - Table of nodes (name | IP | OS | result | notes) — include skipped nodes
   - Any errors encountered and how they were resolved
   - Recommendations

DRAIN RECOVERY PROCEDURE (attempt in order, stop when drain succeeds):
1. Call get_drain_blockers  — understand what is blocking the drain (stuck pods, PDBs, etc.)
2. Call delete_stuck_pods   — force-delete any pods stuck in Terminating state, then retry drain_node.
3. Call force_drain_node    — override PodDisruptionBudgets with --force --disable-eviction.
4. If force_drain_node also fails: uncordon the node (restore it), report the failure with details,
   and STOP — do not continue to other nodes.

SAFETY RULES:
- Never skip the drain step. Never uncordon before the node is Ready.
- If get_node_details shows a node is already NotReady, skip it and flag it.
- Process exactly ONE node at a time. Do not parallelise.
- Always attempt to recover from drain failures before giving up (see DRAIN RECOVERY PROCEDURE).
- If all recovery attempts fail, restore the node (uncordon) and stop with a clear report.
"""


# ── Agent loop ─────────────────────────────────────────────────────────────────

def run_agent() -> None:
    client = anthropic.Anthropic()

    scope_msg = (
        f"Upgrade only node '{TARGET_NODE}'."
        if TARGET_NODE
        else "Upgrade all nodes in the cluster."
    )
    dry_note = " ⚠️  DRY-RUN: no changes will be made." if DRY_RUN else ""

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Please perform a rolling OS upgrade on the current Kubernetes cluster. "
                f"{scope_msg}{dry_note} "
                "Follow the correct K8s drain-before-reboot procedure and produce a "
                "final Markdown report when done."
            ),
        }
    ]

    mode_label = "DRY-RUN" if DRY_RUN else "LIVE"
    print(f"🚀  Starting K8s rolling OS upgrade agent [{mode_label}] …\n")

    turn = 0
    while True:
        turn += 1
        print(f"\n── Turn {turn} " + "─" * 50)

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8096,
            thinking={"type": "adaptive"},
            system=build_system_prompt(),
            tools=TOOLS,
            messages=messages,
        )

        # Append full assistant content (preserves thinking blocks too)
        messages.append({"role": "assistant", "content": response.content})

        # Print visible text
        for block in response.content:
            if block.type == "text" and block.text:
                print(block.text)

        if response.stop_reason == "end_turn":
            print("\n✅  Rolling upgrade agent finished.")
            break

        if response.stop_reason != "tool_use":
            print(f"[WARN] Unexpected stop_reason: {response.stop_reason}")
            break

        # Execute tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name  = block.name
            tool_input = block.input
            print(f"\n🔧  Tool: {tool_name}  args={json.dumps(tool_input)}")

            fn = TOOL_MAP.get(tool_name)
            if fn is None:
                result_text = f"[ERROR] Unknown tool: {tool_name}"
            else:
                try:
                    result_text = fn(tool_input)
                except Exception as exc:  # noqa: BLE001
                    result_text = f"[ERROR] Tool raised exception: {exc}"

            # Truncate very long output to keep context manageable
            if len(result_text) > 6000:
                result_text = result_text[:6000] + "\n… (truncated)"

            preview = result_text[:400] + ("…" if len(result_text) > 400 else "")
            print(preview)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    global DRY_RUN, SSH_USER, TARGET_NODE

    parser = argparse.ArgumentParser(
        description="Agentic K8s rolling OS upgrade powered by Claude."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing upgrades or reboots.",
    )
    parser.add_argument(
        "--ssh-user",
        default="ubuntu",
        metavar="USER",
        help="SSH username for cluster nodes (default: ubuntu).",
    )
    parser.add_argument(
        "--node",
        default=None,
        metavar="NODE_NAME",
        help="Upgrade only this specific node (by Kubernetes node name).",
    )
    args = parser.parse_args()

    DRY_RUN     = args.dry_run
    SSH_USER    = args.ssh_user
    TARGET_NODE = args.node

    if DRY_RUN:
        print("⚠️  DRY-RUN mode enabled — no upgrades or reboots will occur.\n")

    try:
        run_agent()
    except KeyboardInterrupt:
        print("\n\n⛔  Interrupted by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()
