from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from graph.agent import agent_manager
from tools.skills_scanner import refresh_snapshot, scan_skills

router = APIRouter()

ALLOWED_PREFIXES = ("workspace/", "skills/")


class SaveFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str


DEFAULT_TEMPLATES: dict[str, str] = {
    "workspace/AGENTS.md": "# Agent Guide\n\nDescribe repository-specific operating rules here.\n",
    "workspace/IDENTITY.md": "# Identity\n\nRepository Steward\n",
    "workspace/SOUL.md": "# Soul\n\nBe transparent, careful, and repository-focused.\n",
    "workspace/USER.md": "# User Profile\n\nRecord stable user preferences here.\n",
    "workspace/STEWARD.md": "# Steward Role\n\nYou are the single long-lived Steward Agent.\n",
}


def _resolve_path(relative_path: str) -> Path:
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")

    normalized = relative_path.replace("\\", "/").strip("/")
    if normalized == "SKILLS_SNAPSHOT.md":
        normalized = "skills/SKILLS_SNAPSHOT.md"
    if not normalized.startswith(ALLOWED_PREFIXES):
        raise HTTPException(status_code=400, detail="Path is not in the editable whitelist")

    candidate = (agent_manager.base_dir / normalized).resolve()
    base_dir = agent_manager.base_dir.resolve()
    if base_dir not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return candidate


@router.get("/files")
async def read_file(path: str = Query(..., min_length=1)) -> dict[str, str]:
    file_path = _resolve_path(path)
    if not file_path.exists():
        normalized = path.replace("\\", "/").strip("/")
        default_content = DEFAULT_TEMPLATES.get(normalized)
        if default_content is None:
            raise HTTPException(status_code=404, detail="File not found")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(default_content, encoding="utf-8")
    return {
        "path": path.replace("\\", "/"),
        "content": file_path.read_text(encoding="utf-8"),
    }


@router.post("/files")
async def save_file(payload: SaveFileRequest) -> dict[str, Any]:
    file_path = _resolve_path(payload.path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(payload.content, encoding="utf-8")

    normalized = payload.path.replace("\\", "/")
    if normalized.startswith("skills/"):
        refresh_snapshot(agent_manager.base_dir)
    return {"ok": True, "path": normalized}


@router.get("/skills")
async def list_skills() -> list[dict[str, str]]:
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return [skill.__dict__ for skill in scan_skills(agent_manager.base_dir)]
