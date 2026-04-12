"""
Hallucination reviewer — validates agent responses before they reach the user.

The reviewer uses a second LLM call to cross-check the agent's synthesised
answer against the raw data returned by MCP tool calls.  It looks for:

  • Cluster names, resource IDs, or subscription IDs in the answer that do
    not appear in the tool data (fabricated identifiers).
  • Metrics, counts, or status values that contradict the tool data.
  • Confident claims about things for which the tool data shows errors or
    returned no results.
  • Important issues present in the tool data that were silently omitted.

When the reviewer detects problems it produces a corrected answer and
appends a brief transparency note.  When the answer is accurate it is
returned unchanged.

The reviewer fails open: if the LLM call itself fails, the original
(unreviewed) answer is returned so the user always gets a response.

Usage::

    from agents.reviewer import review_response
    final = await review_response(query, tool_results, draft, settings)
"""
from __future__ import annotations

from typing import Any

import structlog
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

from server.auth.credentials import get_azure_credential

logger = structlog.get_logger(__name__)

# Maximum characters of tool data sent to the reviewer (to stay within token budget)
_MAX_TOOL_DATA_CHARS = 8000

_REVIEWER_SYSTEM_PROMPT = """\
You are a hallucination detection reviewer for an AKS (Azure Kubernetes Service) \
health monitoring assistant. Your job is to verify that an AI-generated health \
report is fully grounded in the actual data returned by Azure and Kubernetes API \
tool calls.

You will receive:
1. The user's original question
2. Raw data from tool calls (the ground truth — what the APIs actually returned)
3. The AI assistant's drafted answer

Your task:
- Read the tool data carefully.
- Check every factual claim in the drafted answer:
    • Cluster names, resource group names, subscription IDs
    • Node counts, pod counts, replica counts
    • Status values (Running, Failed, Pending, Degraded, etc.)
    • Metrics (CPU %, memory, etc.)
    • Event messages, error details
    • Upgrade versions, Kubernetes versions
- Identify any claim that is NOT supported by the tool data, contradicts it,
  or is presented with false certainty when the data shows an error.
- Also check for important issues visible in the tool data that the answer
  omits or downplays.

Decision rules:
- If the answer accurately reflects the tool data: return it UNCHANGED.
- If there are hallucinations or significant inaccuracies: correct them
  and append this exact line at the end:
  > ⚠ _This response was reviewed and corrected for factual accuracy._
- If tool data shows errors/unavailable data and the answer claims certainty:
  soften those claims to reflect the uncertainty.
- Never invent new data. Only use what is in the tool results.

IMPORTANT: Return ONLY the final answer in Markdown. No preamble, no
"Here is the corrected answer:", no explanation outside the answer itself.
"""


async def review_response(
    query: str,
    tool_results: str,
    draft_answer: str,
    settings: Any,
) -> str:
    """
    Review the agent's drafted answer for hallucinations.

    Args:
        query:        The original user question.
        tool_results: Concatenated text of all raw MCP tool call results.
        draft_answer: The agent's synthesised Markdown answer.
        settings:     A Settings instance (provides Foundry config).

    Returns:
        The reviewed (and possibly corrected) answer string.
    """
    if not tool_results.strip():
        logger.info("reviewer.skip", reason="no_tool_results_to_ground_check")
        return draft_answer

    if not draft_answer.strip():
        logger.info("reviewer.skip", reason="empty_draft")
        return draft_answer

    try:
        endpoint = settings.azure_foundry_endpoint
        model = settings.azure_foundry_model

        if settings.uses_foundry_key_auth:
            credential: Any = AzureKeyCredential(
                settings.azure_foundry_api_key.get_secret_value()
            )
        else:
            credential = get_azure_credential()

        client = ChatCompletionsClient(endpoint=endpoint, credential=credential)

        # Truncate tool data to stay within token budget
        truncated_tool_results = tool_results[:_MAX_TOOL_DATA_CHARS]
        if len(tool_results) > _MAX_TOOL_DATA_CHARS:
            truncated_tool_results += "\n... [tool data truncated for review]"

        user_content = (
            f"**User question:**\n{query}\n\n"
            f"**Tool data (ground truth from APIs):**\n"
            f"```\n{truncated_tool_results}\n```\n\n"
            f"**Drafted answer to review:**\n{draft_answer}"
        )

        response = client.complete(
            model=model,
            messages=[
                SystemMessage(content=_REVIEWER_SYSTEM_PROMPT),
                UserMessage(content=user_content),
            ],
            max_tokens=4096,
            temperature=0.0,
        )

        reviewed = (response.choices[0].message.content or "").strip()

        if not reviewed:
            logger.warning("reviewer.empty_response")
            return draft_answer

        was_corrected = reviewed != draft_answer.strip()
        logger.info(
            "reviewer.complete",
            corrected=was_corrected,
            original_chars=len(draft_answer),
            reviewed_chars=len(reviewed),
        )
        return reviewed

    except Exception as exc:  # noqa: BLE001
        # Fail open — always return a response to the user
        logger.warning("reviewer.error", error=str(exc))
        return draft_answer
