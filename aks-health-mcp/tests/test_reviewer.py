"""Tests for the hallucination reviewer (agents/reviewer.py)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.reviewer import review_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.azure_foundry_endpoint = "https://test.services.ai.azure.com/models"
    s.azure_foundry_model = "gpt-4o"
    s.uses_foundry_key_auth = False
    return s


SAMPLE_TOOL_RESULTS = """\
=== Tool: aks_list_clusters ===
{"clusters": [{"name": "prod-cluster", "resource_group": "rg-prod",
"provisioning_state": "Succeeded", "kubernetes_version": "1.29.2"}]}

=== Tool: k8s_get_nodes ===
{"nodes": [{"name": "node-001", "status": "Ready", "roles": ["agent"]},
           {"name": "node-002", "status": "Ready", "roles": ["agent"]}]}
"""

SAMPLE_ACCURATE_ANSWER = """\
## Executive Summary
Your AKS environment is healthy.

## Informational
- **prod-cluster** (rg-prod) is running Kubernetes 1.29.2, provisioning state: Succeeded.
- 2 nodes are Ready: node-001, node-002.

## Recommended Actions
No immediate action required.
"""

SAMPLE_HALLUCINATED_ANSWER = """\
## Executive Summary
Your AKS environment has issues.

## Critical Issues
- **dev-cluster** is in a Failed state.  ← (fabricated — not in tool data)
- Node node-999 is NotReady.            ← (fabricated — not in tool data)
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_returns_original_when_accurate() -> None:
    """When the LLM confirms no changes, the original answer is returned."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = SAMPLE_ACCURATE_ANSWER.strip()

    mock_client = MagicMock()
    mock_client.complete.return_value = mock_response

    with (
        patch("agents.reviewer.ChatCompletionsClient", return_value=mock_client),
        patch("agents.reviewer.get_azure_credential"),
    ):
        result = await review_response(
            query="What is the health of my clusters?",
            tool_results=SAMPLE_TOOL_RESULTS,
            draft_answer=SAMPLE_ACCURATE_ANSWER,
            settings=_make_settings(),
        )

    assert result == SAMPLE_ACCURATE_ANSWER.strip()


@pytest.mark.asyncio
async def test_reviewer_corrects_hallucinations() -> None:
    """When the LLM finds hallucinations, a corrected answer is returned."""
    corrected = SAMPLE_ACCURATE_ANSWER + "\n> ⚠ _This response was reviewed and corrected for factual accuracy._"
    mock_response = MagicMock()
    mock_response.choices[0].message.content = corrected

    mock_client = MagicMock()
    mock_client.complete.return_value = mock_response

    with (
        patch("agents.reviewer.ChatCompletionsClient", return_value=mock_client),
        patch("agents.reviewer.get_azure_credential"),
    ):
        result = await review_response(
            query="What is the health of my clusters?",
            tool_results=SAMPLE_TOOL_RESULTS,
            draft_answer=SAMPLE_HALLUCINATED_ANSWER,
            settings=_make_settings(),
        )

    assert "⚠" in result
    assert result == corrected


@pytest.mark.asyncio
async def test_reviewer_skips_when_no_tool_results() -> None:
    """With no tool data there is nothing to ground-check — return draft unchanged."""
    result = await review_response(
        query="What is the health of my clusters?",
        tool_results="",
        draft_answer=SAMPLE_ACCURATE_ANSWER,
        settings=_make_settings(),
    )
    assert result == SAMPLE_ACCURATE_ANSWER


@pytest.mark.asyncio
async def test_reviewer_skips_empty_draft() -> None:
    """Empty draft is returned as-is."""
    result = await review_response(
        query="Any issues?",
        tool_results=SAMPLE_TOOL_RESULTS,
        draft_answer="",
        settings=_make_settings(),
    )
    assert result == ""


@pytest.mark.asyncio
async def test_reviewer_fails_open_on_llm_error() -> None:
    """When the LLM call fails, the original draft is returned unchanged."""
    with (
        patch("agents.reviewer.ChatCompletionsClient", side_effect=RuntimeError("LLM down")),
        patch("agents.reviewer.get_azure_credential"),
    ):
        result = await review_response(
            query="What is the health?",
            tool_results=SAMPLE_TOOL_RESULTS,
            draft_answer=SAMPLE_ACCURATE_ANSWER,
            settings=_make_settings(),
        )

    assert result == SAMPLE_ACCURATE_ANSWER


@pytest.mark.asyncio
async def test_reviewer_fails_open_on_empty_llm_response() -> None:
    """When the LLM returns empty content, the original draft is returned."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = ""

    mock_client = MagicMock()
    mock_client.complete.return_value = mock_response

    with (
        patch("agents.reviewer.ChatCompletionsClient", return_value=mock_client),
        patch("agents.reviewer.get_azure_credential"),
    ):
        result = await review_response(
            query="Any issues?",
            tool_results=SAMPLE_TOOL_RESULTS,
            draft_answer=SAMPLE_ACCURATE_ANSWER,
            settings=_make_settings(),
        )

    assert result == SAMPLE_ACCURATE_ANSWER
