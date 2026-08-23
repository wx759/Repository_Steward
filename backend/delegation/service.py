from __future__ import annotations

import asyncio
from pathlib import Path

from delegation.git_changes import GitChangeTracker, GitStateError
from delegation.models import TaskSpec, WorkerResult
from delegation.permissions import PermissionScope
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
            scope = PermissionScope.for_task(workspace_root, task)
            try:
                tracker = await asyncio.to_thread(GitChangeTracker, workspace_root)
            except GitStateError as exc:
                return WorkerResult(
                    status="failed",
                    summary="Delegation requires a readable Git repository.",
                    issues=[str(exc)],
                )
            feedback = ""
            result = WorkerResult(status="failed", summary="Worker did not run")
            for _round in range(self.MAX_REVIEW_ROUNDS):
                result = await self.worker_factory.run(
                    workspace_root,
                    task,
                    feedback=feedback,
                )
                try:
                    changes = await asyncio.to_thread(tracker.changes)
                except GitStateError as exc:
                    return result.model_copy(
                        update={"status": "failed", "issues": [str(exc)]}
                    )
                denied = [path for path in changes.paths if not scope.can_write(path)]
                observed = result.model_copy(update={"changed_files": list(changes.paths)})
                if denied:
                    return observed.model_copy(
                        update={
                            "status": "failed",
                            "issues": list(
                                dict.fromkeys(
                                    result.issues
                                    + ["PermissionScope violation: " + ", ".join(denied)]
                                )
                            ),
                        }
                    )
                review = await self.reviewer.review(
                    workspace_root,
                    task,
                    observed,
                    task_diff=changes.diff,
                )
                if review.approved:
                    final_changes = await asyncio.to_thread(tracker.changes)
                    final_denied = [
                        path for path in final_changes.paths if not scope.can_write(path)
                    ]
                    if final_denied:
                        return observed.model_copy(
                            update={
                                "status": "failed",
                                "changed_files": list(final_changes.paths),
                                "issues": [
                                    "PermissionScope violation after review: "
                                    + ", ".join(final_denied)
                                ],
                            }
                        )
                    return observed.model_copy(
                        update={
                            "status": "success",
                            "changed_files": list(final_changes.paths),
                        }
                    )
                result = observed
                feedback = review.feedback or "; ".join(review.issues)
            return result.model_copy(
                update={
                    "status": "failed",
                    "issues": list(dict.fromkeys(result.issues + [feedback or "Review failed"])),
                }
            )
