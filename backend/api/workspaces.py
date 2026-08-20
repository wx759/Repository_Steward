from __future__ import annotations

from dataclasses import asdict
import subprocess
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from graph.agent import agent_manager

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    root_path: str = Field(..., min_length=1)
    name: str | None = Field(default=None, max_length=100)
    require_git: bool = False


@router.get("/workspaces/config")
async def workspace_config() -> dict[str, str]:
    return {"workspace_root": str(_manager().workspace_root)}


@router.get("/workspaces/discover")
async def discover_workspaces() -> list[dict[str, str]]:
    manager = _manager()
    internal_root = agent_manager.base_dir.parent.resolve() if agent_manager.base_dir else None
    candidates: list[dict[str, str]] = []
    ignored = {"node_modules", ".venv", "venv", "__pycache__", ".cache"}
    for current, directories, _files in os.walk(manager.workspace_root):
        path = Path(current).resolve()
        depth = len(path.relative_to(manager.workspace_root).parts)
        directories[:] = [item for item in directories if item not in ignored]
        if ".git" in directories:
            directories.remove(".git")
            if path != internal_root:
                candidates.append(
                    {
                        "name": path.name,
                        "root_path": str(path),
                        "relative_path": path.relative_to(manager.workspace_root).as_posix(),
                    }
                )
        if depth >= 3:
            directories.clear()
    return sorted(candidates, key=lambda item: item["name"].lower())


def _manager():
    if agent_manager.workspace_manager is None:
        raise HTTPException(status_code=503, detail="Workspace manager is not initialized")
    return agent_manager.workspace_manager


def _response(workspace) -> dict:
    payload = asdict(workspace)
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=Path(workspace.root_path),
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    payload["branch"] = completed.stdout.strip() or "—"
    return payload


@router.get("/workspaces")
async def list_workspaces() -> list[dict]:
    if agent_manager.base_dir is None:
        raise HTTPException(status_code=503, detail="Agent manager is not initialized")
    return [
        _response(item)
        for item in _manager().list_workspaces()
        if Path(item.root_path).resolve() != agent_manager.base_dir.parent.resolve()
    ]


@router.post("/workspaces")
async def create_workspace(payload: CreateWorkspaceRequest) -> dict:
    try:
        return _response(
            _manager().create_workspace(
                payload.root_path,
                name=payload.name,
                require_git=payload.require_git,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
