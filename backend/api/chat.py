from __future__ import annotations

import json
import traceback
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from graph.agent import agent_manager

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str
    stream: bool = True


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(payload: ChatRequest):
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    record = session_manager.load_session_record(payload.session_id)
    history = await agent_manager.load_checkpoint_seed(payload.session_id)
    if agent_manager.run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager is not initialized")
    try:
        workspace = agent_manager._workspace_for_session(payload.session_id)
        run = agent_manager.run_manager.create_run(
            payload.session_id,
            workspace.workspace_id,
            payload.message,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    is_first_user_message = not any(
        message.get("role") == "user" for message in record.get("messages", [])
    )

    async def events() -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"type": "run", "run_id": run["run_id"], "status": "running"}
            async for event in agent_manager.astream(
                payload.message,
                history,
                session_id=payload.session_id,
                run_id=run["run_id"],
            ):
                event_type = event["type"]
                if event_type == "done":
                    session_messages = event.get("_session_messages", [])
                    if session_messages:
                        session_manager.append_agent_messages(
                            payload.session_id,
                            session_messages,
                        )
                    if event.get("_reset_checkpoint"):
                        await agent_manager.reset_checkpoint(payload.session_id)

                yield event

                if (
                    event_type == "done"
                    and event.get("status") != "interrupted"
                    and is_first_user_message
                ):
                    title = await agent_manager.generate_title(payload.message)
                    session_manager.set_title(payload.session_id, title)
                    yield {
                        "type": "title",
                        "session_id": payload.session_id,
                        "title": title,
                    }
        except Exception as exc:
            traceback.print_exc()
            agent_manager.run_manager.fail_run(run["run_id"], str(exc))
            yield {"type": "error", "error": str(exc)}

    if payload.stream:
        async def event_stream() -> AsyncIterator[str]:
            async for event in events():
                event_type = str(event.get("type", "message"))
                yield _sse(
                    event_type,
                    {
                        key: value
                        for key, value in event.items()
                        if key != "type" and not key.startswith("_")
                    },
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    final_content = ""
    async for event in events():
        if event["type"] == "error":
            return JSONResponse({"error": event["error"]}, status_code=500)
        if event["type"] == "done":
            final_content = str(event.get("content", ""))
    return JSONResponse({"content": final_content})
