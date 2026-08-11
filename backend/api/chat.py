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


def _new_segment() -> dict[str, Any]:
    return {"content": "", "tool_calls": []}


@router.post("/chat")
async def chat(payload: ChatRequest):
    session_manager = agent_manager.session_manager
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    record = session_manager.load_session_record(payload.session_id)
    history = session_manager.load_session_for_agent(payload.session_id)
    is_first_user_message = not any(
        message.get("role") == "user" for message in record.get("messages", [])
    )

    async def events() -> AsyncIterator[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        current_segment = _new_segment()
        try:
            async for event in agent_manager.astream(payload.message, history):
                event_type = event["type"]
                if event_type == "token":
                    current_segment["content"] += event.get("content", "")
                elif event_type == "tool_start":
                    current_segment["tool_calls"].append(
                        {
                            "tool": event.get("tool", "tool"),
                            "input": event.get("input", ""),
                            "output": "",
                        }
                    )
                elif event_type == "tool_end" and current_segment["tool_calls"]:
                    current_segment["tool_calls"][-1]["output"] = event.get("output", "")
                elif event_type == "new_response":
                    if current_segment["content"].strip() or current_segment["tool_calls"]:
                        segments.append(current_segment)
                    current_segment = _new_segment()
                elif event_type == "done":
                    if not current_segment["content"].strip() and event.get("content"):
                        current_segment["content"] = event["content"]
                    if current_segment["content"].strip() or current_segment["tool_calls"]:
                        segments.append(current_segment)

                    session_manager.save_message(payload.session_id, "user", payload.message)
                    for segment in segments:
                        session_manager.save_message(
                            payload.session_id,
                            "assistant",
                            segment["content"],
                            tool_calls=segment["tool_calls"] or None,
                        )

                yield event

                if event_type == "done" and is_first_user_message:
                    title = await agent_manager.generate_title(payload.message)
                    session_manager.set_title(payload.session_id, title)
                    yield {
                        "type": "title",
                        "session_id": payload.session_id,
                        "title": title,
                    }
        except Exception as exc:
            traceback.print_exc()
            yield {"type": "error", "error": str(exc)}

    if payload.stream:
        async def event_stream() -> AsyncIterator[str]:
            async for event in events():
                event_type = str(event.get("type", "message"))
                yield _sse(
                    event_type,
                    {key: value for key, value in event.items() if key != "type"},
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    final_content = ""
    async for event in events():
        if event["type"] == "error":
            return JSONResponse({"error": event["error"]}, status_code=500)
        if event["type"] == "done":
            final_content = str(event.get("content", ""))
    return JSONResponse({"content": final_content})
