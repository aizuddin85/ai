#!/usr/bin/env python3
"""
Agentic Kubernetes Health Check
Uses Claude with tool use to autonomously inspect a K8s cluster
and perform health checks across all namespaces.
"""

import json
import subprocess
import sys
import anthropic

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_current_context",
        "description": (
            "Get the current kubectl context, cluster name, server URL, "
            "and the active user/namespace."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_namespaces",
        "description": "List all namespaces in the cluster.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_pods",
        "description": (
            "Get the status of all pods in a namespace. "
            "Returns pod name, status, ready state, restarts, and age."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to inspect.",
                }
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_deployments",
        "description": (
            "Get the status of all deployments in a namespace. "
            "Returns name, desired/available/ready replicas, and age."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to inspect.",
                }
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_services",
        "description": "List all services in a namespace with their type, cluster IP, and ports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to inspect.",
                }
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_events",
        "description": (
            "Fetch recent warning/error events in a namespace. "
            "Useful for diagnosing unhealthy resources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to inspect.",
                }
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "check_nodes",
        "description": "Get the status and resource usage of all cluster nodes.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_resource",
        "description": (
            "Run 'kubectl describe' on a specific resource for deep inspection. "
            "Use when a resource looks unhealthy and you need more details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": "Resource type, e.g. pod, deployment, node.",
                },
                "resource_name": {
                    "type": "string",
                    "description": "Name of the resource.",
                },
                "namespace": {
                    "type": "string",
                    "description": "Namespace (omit for cluster-scoped resources like nodes).",
                },
            },
            "required": ["resource_type", "resource_name"],
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

def _run(cmd: list[str]) -> str:
    """Execute a kubectl command and return combined stdout/stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip()
            return f"[ERROR exit={result.returncode}]\n{error}"
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "[ERROR] Command timed out after 30 s"
    except FileNotFoundError:
        return "[ERROR] kubectl not found. Is it installed and on PATH?"


def get_current_context(_: dict) -> str:
    context = _run(["kubectl", "config", "current-context"])
    cluster = _run(["kubectl", "config", "view",
                    "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"])
    user = _run(["kubectl", "config", "view",
                 "--minify", "-o", "jsonpath={.users[0].name}"])
    namespace = _run(["kubectl", "config", "view",
                      "--minify", "-o",
                      "jsonpath={.contexts[0].context.namespace}"])
    return (
        f"Context:   {context}\n"
        f"Server:    {cluster}\n"
        f"User:      {user}\n"
        f"Namespace: {namespace or 'default'}"
    )


def list_namespaces(_: dict) -> str:
    return _run(["kubectl", "get", "namespaces", "--no-headers",
                 "-o", "custom-columns=NAME:.metadata.name,STATUS:.status.phase,AGE:.metadata.creationTimestamp"])


def check_pods(args: dict) -> str:
    ns = args["namespace"]
    return _run([
        "kubectl", "get", "pods", "-n", ns,
        "-o", "wide", "--no-headers",
        "--sort-by=.status.phase",
    ])


def check_deployments(args: dict) -> str:
    ns = args["namespace"]
    return _run([
        "kubectl", "get", "deployments", "-n", ns,
        "-o", "custom-columns="
        "NAME:.metadata.name,"
        "DESIRED:.spec.replicas,"
        "AVAILABLE:.status.availableReplicas,"
        "READY:.status.readyReplicas,"
        "AGE:.metadata.creationTimestamp",
    ])


def check_services(args: dict) -> str:
    ns = args["namespace"]
    return _run(["kubectl", "get", "services", "-n", ns])


def check_events(args: dict) -> str:
    ns = args["namespace"]
    return _run([
        "kubectl", "get", "events", "-n", ns,
        "--field-selector=type=Warning",
        "--sort-by=.lastTimestamp",
    ])


def check_nodes(_: dict) -> str:
    return _run([
        "kubectl", "get", "nodes",
        "-o", "custom-columns="
        "NAME:.metadata.name,"
        "STATUS:.status.conditions[-1].type,"
        "ROLES:.metadata.labels.kubernetes\\.io/role,"
        "AGE:.metadata.creationTimestamp,"
        "VERSION:.status.nodeInfo.kubeletVersion",
    ])


def describe_resource(args: dict) -> str:
    cmd = ["kubectl", "describe", args["resource_type"], args["resource_name"]]
    if args.get("namespace"):
        cmd += ["-n", args["namespace"]]
    return _run(cmd)


TOOL_MAP = {
    "get_current_context": get_current_context,
    "list_namespaces": list_namespaces,
    "check_pods": check_pods,
    "check_deployments": check_deployments,
    "check_services": check_services,
    "check_events": check_events,
    "check_nodes": check_nodes,
    "describe_resource": describe_resource,
}


# ── Agent loop ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert Kubernetes SRE (Site Reliability Engineer).
Your job is to perform a comprehensive health check of the Kubernetes cluster
accessible via kubectl.

Follow this strategy:
1. Identify the current cluster context (server, user, namespace).
2. Check all cluster nodes for readiness.
3. List all namespaces.
4. For each namespace, sequentially:
   a. Check pod status — flag any not in Running/Completed state or with high restart counts.
   b. Check deployment health — flag any deployments with unavailable replicas.
   c. Check services.
   d. Fetch warning events.
   e. If any resource looks unhealthy, use describe_resource for details.
5. After inspecting every namespace, write a structured Markdown health report:
   - Executive summary (overall cluster health: Healthy / Degraded / Critical)
   - Nodes section
   - Per-namespace section (pods, deployments, notable events)
   - Issues found (severity: Critical / Warning / Info)
   - Recommendations

Be thorough. Do not skip namespaces. Use the tools as many times as needed.
"""


def run_agent() -> None:
    client = anthropic.Anthropic()

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "Please perform a full health check on the current Kubernetes cluster. "
                "Inspect every namespace and produce a detailed Markdown report."
            ),
        }
    ]

    print("🔍  Starting Kubernetes health-check agent …\n")

    turn = 0
    while True:
        turn += 1
        print(f"── Turn {turn} ──────────────────────────────────")

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Append full assistant content to history
        messages.append({"role": "assistant", "content": response.content})

        # Print any visible text blocks
        for block in response.content:
            if block.type == "text" and block.text:
                print(block.text)

        # Done?
        if response.stop_reason == "end_turn":
            print("\n✅  Health check complete.")
            break

        # Execute tool calls
        if response.stop_reason != "tool_use":
            print(f"[WARN] Unexpected stop_reason: {response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            print(f"\n🔧  Tool: {tool_name}  args={json.dumps(tool_input)}")

            fn = TOOL_MAP.get(tool_name)
            if fn is None:
                result_text = f"[ERROR] Unknown tool: {tool_name}"
            else:
                result_text = fn(tool_input)

            # Truncate very long output so we stay within context limits
            if len(result_text) > 4000:
                result_text = result_text[:4000] + "\n… (truncated)"

            print(result_text[:500] + ("…" if len(result_text) > 500 else ""))

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    run_agent()
