from __future__ import annotations

import asyncio
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from graph.agent import agent_manager
from memory import MemoryStatus, MemoryType

router = APIRouter()


class ArchiveMemoryRequest(BaseModel):
    workspace_id: str


def _store():
    if agent_manager.memory_store is None:
        raise HTTPException(status_code=503, detail="Long-term memory is disabled")
    return agent_manager.memory_store


def _validate_workspace(workspace_id: str) -> None:
    if agent_manager.workspace_manager is None:
        raise HTTPException(status_code=503, detail="Workspace manager is not initialized")
    try:
        agent_manager.workspace_manager.get_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc


@router.get("/memories")
async def list_memories(
    workspace_id: str = Query(..., min_length=1),
    status: MemoryStatus | None = None,
    memory_type: MemoryType | None = Query(default=None, alias="type"),
) -> list[dict]:
    _validate_workspace(workspace_id)
    records = await asyncio.to_thread(
        _store().list_scoped_memories,
        workspace_id,
        status=status,
    )
    if memory_type is not None:
        records = [record for record in records if record.type == memory_type]
    return [asdict(record) for record in records]


@router.post("/memories/{memory_id}/archive")
async def archive_memory(memory_id: str, payload: ArchiveMemoryRequest) -> dict[str, bool]:
    _validate_workspace(payload.workspace_id)
    store = _store()
    active = await asyncio.to_thread(
        store.list_scoped_memories,
        payload.workspace_id,
        status="active",
    )
    if memory_id not in {record.id for record in active}:
        raise HTTPException(status_code=404, detail="Active memory not found in this workspace")
    archived = await asyncio.to_thread(store.archive_memory, memory_id)
    if not archived:
        raise HTTPException(status_code=409, detail="Memory is no longer active")
    return {"ok": True}
