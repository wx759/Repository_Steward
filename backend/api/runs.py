from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from graph.agent import agent_manager

router = APIRouter()


@router.get("/runs")
async def list_runs(session_id: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    if agent_manager.run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager is not initialized")
    return agent_manager.run_manager.list_runs(session_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    if agent_manager.run_manager is None:
        raise HTTPException(status_code=503, detail="Run manager is not initialized")
    try:
        return agent_manager.cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
