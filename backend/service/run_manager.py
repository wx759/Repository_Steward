from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from delegation.models import TaskSpec, WorkerResult


class RunManager:
    """JSON-backed lifecycle store for one Agent execution per user request."""

    def __init__(self, base_dir: Path) -> None:
        self.runs_dir = base_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._active_by_session: dict[str, str] = {}

    def _path(self, run_id: str) -> Path:
        if not run_id.isalnum():
            raise ValueError("Invalid run id")
        return self.runs_dir / f"{run_id}.json"

    def _write(self, run: dict[str, Any]) -> None:
        run["updated_at"] = time.time()
        self._path(run["run_id"]).write_text(
            json.dumps(run, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read(self, run_id: str) -> dict[str, Any]:
        return json.loads(self._path(run_id).read_text(encoding="utf-8"))

    def create_run(self, session_id: str, workspace_id: str, goal: str) -> dict[str, Any]:
        active = self.get_active_run(session_id)
        if active is not None:
            raise RuntimeError("A run is already active for this session")
        now = time.time()
        run = {
            "run_id": uuid.uuid4().hex,
            "session_id": session_id,
            "workspace_id": workspace_id,
            "goal": goal,
            "status": "running",
            "reason": None,
            "error": None,
            "tasks": [],
            "created_at": now,
            "updated_at": now,
        }
        self._active_by_session[session_id] = run["run_id"]
        self._write(run)
        return run

    def ensure_run(self, session_id: str, workspace_id: str, goal: str) -> dict[str, Any]:
        """Return the request Run; retained for delegate_task integration."""
        active_id = self._active_by_session.get(session_id)
        if active_id and self._path(active_id).exists():
            run = self._read(active_id)
            if run.get("status") == "running":
                return run
        return self.create_run(session_id, workspace_id, goal)

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.exists():
            raise KeyError(f"Run not found: {run_id}")
        return self._read(run_id)

    def get_active_run(self, session_id: str) -> dict[str, Any] | None:
        run_id = self._active_by_session.get(session_id)
        if not run_id or not self._path(run_id).exists():
            return None
        run = self._read(run_id)
        if run.get("status") != "running":
            self._active_by_session.pop(session_id, None)
            return None
        return run

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        reason: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"Invalid terminal run status: {status}")
        run = self.get_run(run_id)
        if run.get("status") == "running":
            run["status"] = status
            run["reason"] = reason
            run["error"] = error
            self._write(run)
        if self._active_by_session.get(str(run["session_id"])) == run_id:
            self._active_by_session.pop(str(run["session_id"]), None)
        return run

    def interrupt_run(self, run_id: str, reason: str = "user_cancelled") -> dict[str, Any]:
        return self.finish_run(run_id, "interrupted", reason=reason)

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        return self.finish_run(run_id, "failed", reason="execution_error", error=error)

    def add_task(self, run_id: str, task: TaskSpec) -> str:
        run = self._read(run_id)
        task_id = uuid.uuid4().hex
        run["tasks"].append(
            {
                "task_id": task_id,
                "role": task.role,
                "description": task.description,
                "status": "running",
                "review_status": "pending",
                "result": None,
            }
        )
        self._write(run)
        return task_id

    def finish_task(self, run_id: str, task_id: str, result: WorkerResult) -> None:
        run = self._read(run_id)
        for task in run["tasks"]:
            if task["task_id"] == task_id:
                task["status"] = "completed" if result.status == "success" else "failed"
                task["review_status"] = "passed" if result.status == "success" else "failed"
                task["result"] = result.model_dump()
                break
        if result.status == "failed":
            run["status"] = "failed"
        self._write(run)

    def finish_session_run(
        self,
        session_id: str,
        *,
        failed: bool = False,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        run_id = self._active_by_session.get(session_id)
        if not run_id or not self._path(run_id).exists():
            return
        self.finish_run(
            run_id,
            "failed" if failed else "completed",
            reason=reason,
            error=error,
        )

    def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if run.get("session_id") == session_id:
                runs.append(run)
        return sorted(runs, key=lambda item: item.get("updated_at", 0), reverse=True)
