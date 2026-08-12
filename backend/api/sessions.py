from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graph.agent import agent_manager
from service.prompt_builder import build_system_prompt

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: str = "新会话"


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class GenerateTitleRequest(BaseModel):
    message: str | None = None


def _session_manager():
    manager = agent_manager.session_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return manager


@router.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return _session_manager().list_sessions()


@router.post("/sessions")
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    return _session_manager().create_session(title=payload.title)


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, payload: RenameSessionRequest) -> dict[str, Any]:
    return _session_manager().rename_session(session_id, payload.title)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, bool]:
    await agent_manager.delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> dict[str, Any]:
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return {
        "system_prompt": build_system_prompt(agent_manager.base_dir),
        "messages": _session_manager().get_history(session_id)["messages"],
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str) -> dict[str, Any]:
    return _session_manager().get_history(session_id)


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str, payload: GenerateTitleRequest) -> dict[str, str]:
    manager = _session_manager()
    messages = manager.get_history(session_id)["messages"]
    seed = payload.message or next(
        (str(item.get("content", "")) for item in messages if item.get("role") == "user"),
        "",
    )
    title = await agent_manager.generate_title(seed or "新会话")
    manager.set_title(session_id, title)
    return {"session_id": session_id, "title": title}
