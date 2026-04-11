"""
/api/agent endpoints – query the RootAgent and stream results via SSE.

SSE event protocol
------------------
Every event is a JSON object sent as:
    data: <json>\n\n

Event types:
  {"type": "started",  "query_id": "<uuid>"}
  {"type": "status",   "query_id": "<uuid>", "message": "<text>"}
  {"type": "result",   "query_id": "<uuid>", "content": "<markdown>"}
  {"type": "error",    "query_id": "<uuid>", "message": "<text>"}
  {"type": "done",     "query_id": "<uuid>"}

The frontend listens for 'result' to render the markdown report.
'status' events are shown as progress messages while the agent works.
'error' events surface agent/tool failures without killing the stream.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.root_agent import RootAgent
from api.auth.azure_ad import AuthenticatedUser, get_current_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# Maximum concurrent agent calls per process (each spawns MCP sub-processes)
_AGENT_SEMAPHORE = asyncio.Semaphore(5)


class QueryRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Natural-language AKS health question.",
    )


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(event: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


async def _run_agent_stream(
    query: str,
    query_id: str,
    user: AuthenticatedUser,
) -> AsyncGenerator[str, None]:
    """
    Drive the RootAgent and emit SSE events.

    Yields:
        SSE-formatted strings to be sent to the browser.
    """
    log = logger.bind(query_id=query_id, upn=user.upn)
    log.info("agent.query.start", query=query[:200])

    yield _sse({"type": "started", "query_id": query_id})
    yield _sse({
        "type": "status",
        "query_id": query_id,
        "message": "Initialising agent and connecting to MCP server…",
    })

    async with _AGENT_SEMAPHORE:
        # Create the heartbeat task before the try block so it is always
        # reachable in the finally clause and can be properly cancelled.
        heartbeat_task = asyncio.create_task(_heartbeat(query_id))
        try:
            yield _sse({
                "type": "status",
                "query_id": query_id,
                "message": "Querying Azure and cluster health (this may take 15–30 s)…",
            })

            agent = RootAgent()
            result = await agent.run(query)

            log.info("agent.query.done", result_length=len(result))
            yield _sse({"type": "result", "query_id": query_id, "content": result})

        except asyncio.CancelledError:
            log.info("agent.query.cancelled")
            yield _sse({
                "type": "error",
                "query_id": query_id,
                "message": "Query was cancelled.",
            })
        except Exception as exc:  # noqa: BLE001
            log.error("agent.query.error", error=str(exc))
            yield _sse({
                "type": "error",
                "query_id": query_id,
                "message": f"Agent error: {exc}",
            })
        finally:
            # Always cancel the heartbeat and emit the terminal event.
            heartbeat_task.cancel()
            yield _sse({"type": "done", "query_id": query_id})


async def _heartbeat(query_id: str) -> None:
    """Dummy task – not yielded; kept so the generator doesn't close early."""
    while True:
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/query")
async def query_agent(
    body: QueryRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> StreamingResponse:
    """
    Submit an AKS health query to the RootAgent.

    Returns a Server-Sent Events stream.  The browser should open this
    with EventSource or fetch() + ReadableStream.

    All calls are authenticated and group-authorised via the bearer token
    in the Authorization header.
    """
    query_id = str(uuid.uuid4())
    logger.info(
        "api.agent.query",
        query_id=query_id,
        upn=user.upn,
        query=body.query[:100],
    )

    return StreamingResponse(
        _run_agent_stream(body.query, query_id, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
        },
    )


@router.get("/history")
async def get_history(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """
    Placeholder for query history.
    In production this would return persisted queries from a database.
    """
    return {"queries": [], "user": user.upn}
