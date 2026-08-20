import asyncio
from pathlib import Path

from delegation.models import ReviewResult, TaskSpec, WorkerResult
from delegation.permissions import ReviewerTerminalTool, ScopedWriteFileTool
from delegation.service import DelegationService


class FakeWorkerFactory:
    def __init__(self) -> None:
        self.feedback: list[str] = []

    async def run(self, _root, _task, *, feedback=""):
        self.feedback.append(feedback)
        return WorkerResult(
            status="success",
            summary="implemented",
            changed_files=["backend/api.py"],
            tests_run=["pytest: passed"],
        )


class FakeReviewer:
    def __init__(self, approvals: list[bool]) -> None:
        self.approvals = approvals

    async def review(self, _root, _task, _result):
        approved = self.approvals.pop(0)
        return ReviewResult(
            approved=approved,
            issues=[] if approved else ["missing assertion"],
            feedback="add assertion" if not approved else "",
        )


def task() -> TaskSpec:
    return TaskSpec(
        role="backend",
        description="implement endpoint",
        allowed_paths=["backend/**"],
        acceptance_criteria=["tests pass"],
    )


def test_review_feedback_retries_worker_then_returns_structured_result(tmp_path: Path) -> None:
    worker = FakeWorkerFactory()
    service = DelegationService(worker, FakeReviewer([False, True]))  # type: ignore[arg-type]
    result = asyncio.run(service.delegate(tmp_path, task()))
    assert result.status == "success"
    assert worker.feedback == ["", "add assertion"]
    assert result.model_dump() == {
        "status": "success",
        "summary": "implemented",
        "changed_files": ["backend/api.py"],
        "tests_run": ["pytest: passed"],
        "issues": [],
    }


def test_permission_scope_rejects_worker_escape_and_reviewer_mutation(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    writer = ScopedWriteFileTool(tmp_path, ["backend/**"])
    assert "Wrote" in writer.invoke({"path": "backend/ok.py", "content": "ok"})
    assert "denied" in writer.invoke({"path": "frontend/no.ts", "content": "no"}).lower()
    reviewer = ReviewerTerminalTool(tmp_path)
    assert "denied" in reviewer.invoke({"command": "touch changed.txt"}).lower()


def test_review_stops_after_maximum_rounds(tmp_path: Path) -> None:
    worker = FakeWorkerFactory()
    service = DelegationService(worker, FakeReviewer([False, False]))  # type: ignore[arg-type]
    result = asyncio.run(service.delegate(tmp_path, task()))
    assert result.status == "failed"
    assert len(worker.feedback) == 2
