import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from delegation.git_changes import GitChangeTracker
from delegation.models import ReviewResult, TaskSpec, WorkerResult
from delegation.permissions import (
    PermissionScope,
    ScopedWriteFileTool,
    StructuredCommandTool,
)
from delegation.service import DelegationService


def init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


class FakeWorkerFactory:
    def __init__(self, changed_path: str = "backend/api.py") -> None:
        self.feedback: list[str] = []
        self.changed_path = changed_path

    async def run(self, root, _task, *, feedback=""):
        self.feedback.append(feedback)
        target = Path(root) / self.changed_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("implemented\n", encoding="utf-8")
        return WorkerResult(
            status="success",
            summary="implemented",
            changed_files=["untrusted/model-report.py"],
            tests_run=["pytest: passed"],
        )


class FakeReviewer:
    def __init__(self, approvals: list[bool]) -> None:
        self.approvals = approvals
        self.diffs: list[str] = []

    async def review(self, _root, _task, _result, *, task_diff):
        self.diffs.append(task_diff)
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
        allowed_commands=["pytest"],
        acceptance_criteria=["tests pass"],
    )


def test_review_feedback_retries_worker_and_uses_observed_changes(tmp_path: Path) -> None:
    init_git(tmp_path)
    worker = FakeWorkerFactory()
    reviewer = FakeReviewer([False, True])
    service = DelegationService(worker, reviewer)  # type: ignore[arg-type]

    result = asyncio.run(service.delegate(tmp_path, task()))

    assert result.status == "success"
    assert worker.feedback == ["", "add assertion"]
    assert result.changed_files == ["backend/api.py"]
    assert result.tests_run == ["pytest: passed"]
    assert "Untracked files created by task" in reviewer.diffs[0]


def test_permission_scope_intersects_repository_role_and_task(tmp_path: Path) -> None:
    scope = PermissionScope.for_task(
        tmp_path,
        TaskSpec(
            role="backend",
            description="auth",
            allowed_paths=["backend/auth/**", "frontend/**"],
            allowed_commands=["pytest", "npm_build"],
        ),
    )
    writer = ScopedWriteFileTool(scope)

    assert scope.can_write("backend/auth/api.py")
    assert not scope.can_write("backend/other.py")
    assert not scope.can_write("frontend/app.ts")
    assert not scope.can_write("backend/auth/.env")
    assert scope.can_run("pytest")
    assert not scope.can_run("npm_build")
    assert "Wrote" in writer.invoke({"path": "backend/auth/api.py", "content": "ok"})
    assert "denied" in writer.invoke(
        {"path": "frontend/no.ts", "content": "no"}
    ).lower()


def test_general_worker_requires_explicit_paths_and_paths_are_relative(tmp_path: Path) -> None:
    scope = PermissionScope.for_task(tmp_path, TaskSpec(role="general", description="change"))
    assert not scope.can_write("anything.py")
    with pytest.raises(ValueError, match="repository-relative"):
        TaskSpec(role="backend", description="bad", allowed_paths=["C:\\secrets\\**"])


def test_structured_command_rejects_shell_text_and_unrequested_command(tmp_path: Path) -> None:
    scope = PermissionScope.for_task(tmp_path, task())
    command = StructuredCommandTool(scope)

    assert "denied" in command.invoke(
        {"command": "npm_build", "targets": []}
    ).lower()
    assert "denied" in command.invoke(
        {"command": "pytest", "targets": ["../outside.py"]}
    ).lower()
    with pytest.raises(Exception):
        command.invoke({"command": "echo hacked > file", "targets": []})


def test_structured_command_uses_argv_without_shell(tmp_path: Path, monkeypatch) -> None:
    scope = PermissionScope.for_task(tmp_path, task())
    command = StructuredCommandTool(scope)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr("delegation.permissions.subprocess.run", fake_run)
    output = command.invoke(
        {"command": "pytest", "targets": ["backend/tests/test_api.py::test_login"]}
    )

    assert captured["argv"] == [
        "python",
        "-m",
        "pytest",
        "backend/tests/test_api.py::test_login",
    ]
    assert captured["kwargs"]["shell"] is False
    assert "exit_code=0" in output


def test_git_tracker_ignores_preexisting_dirty_state(tmp_path: Path) -> None:
    init_git(tmp_path)
    existing = tmp_path / "backend" / "existing.py"
    existing.parent.mkdir()
    existing.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    existing.write_text("user change\n", encoding="utf-8")

    tracker = GitChangeTracker(tmp_path)
    created = tmp_path / "backend" / "worker.py"
    created.write_text("worker change\n", encoding="utf-8")

    assert tracker.changes().paths == ("backend/worker.py",)


def test_out_of_scope_git_change_fails_before_review(tmp_path: Path) -> None:
    init_git(tmp_path)
    reviewer = FakeReviewer([True])
    service = DelegationService(
        FakeWorkerFactory("frontend/app.ts"),
        reviewer,  # type: ignore[arg-type]
    )

    result = asyncio.run(service.delegate(tmp_path, task()))

    assert result.status == "failed"
    assert result.changed_files == ["frontend/app.ts"]
    assert "PermissionScope violation" in result.issues[0]
    assert reviewer.approvals == [True]


def test_review_stops_after_maximum_rounds(tmp_path: Path) -> None:
    init_git(tmp_path)
    worker = FakeWorkerFactory()
    service = DelegationService(worker, FakeReviewer([False, False]))  # type: ignore[arg-type]
    result = asyncio.run(service.delegate(tmp_path, task()))
    assert result.status == "failed"
    assert len(worker.feedback) == 2
