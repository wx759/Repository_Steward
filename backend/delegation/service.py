from __future__ import annotations

import asyncio
from pathlib import Path

from delegation.models import TaskSpec, WorkerResult
from delegation.reviewer import Reviewer
from delegation.worker_factory import WorkerFactory


class DelegationService:
    MAX_REVIEW_ROUNDS = 2

    def __init__(self, worker_factory: WorkerFactory, reviewer: Reviewer) -> None:
        self.worker_factory = worker_factory
        self.reviewer = reviewer
        self._lock = asyncio.Lock()

    async def delegate(self, workspace_root: Path, task: TaskSpec) -> WorkerResult:
        if self._lock.locked():
            return WorkerResult(
                status="failed",
                summary="Another delegated task is already running; delegation is serial.",
                issues=["parallel delegation rejected"],
            )
        async with self._lock:
            feedback = ""
            result = WorkerResult(status="failed", summary="Worker did not run")
            for _round in range(self.MAX_REVIEW_ROUNDS):
                result = await self.worker_factory.run(
                    workspace_root,
                    task,
                    feedback=feedback,
                )
                review = await self.reviewer.review(workspace_root, task, result)
                if review.approved:
                    return result.model_copy(update={"status": "success"})
                feedback = review.feedback or "; ".join(review.issues)
            return result.model_copy(
                update={
                    "status": "failed",
                    "issues": list(dict.fromkeys(result.issues + [feedback or "Review failed"])),
                }
            )
