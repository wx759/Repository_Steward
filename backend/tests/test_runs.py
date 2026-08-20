from pathlib import Path

from delegation.models import TaskSpec, WorkerResult
from service.run_manager import RunManager


def test_run_manager_tracks_task_and_review_progress(tmp_path: Path) -> None:
    manager = RunManager(tmp_path)
    run = manager.ensure_run("session", "workspace", "implement login")
    task_id = manager.add_task(
        run["run_id"],
        TaskSpec(role="backend", description="login API"),
    )
    manager.finish_task(
        run["run_id"],
        task_id,
        WorkerResult(status="success", summary="done", tests_run=["2 passed"]),
    )
    manager.finish_session_run("session")
    saved = manager.list_runs("session")[0]
    assert saved["status"] == "completed"
    assert saved["tasks"][0]["status"] == "completed"
    assert saved["tasks"][0]["review_status"] == "passed"


def test_run_manager_tracks_each_request_lifecycle_and_interrupt_reason(tmp_path: Path) -> None:
    manager = RunManager(tmp_path)
    run = manager.create_run("session", "workspace", "long task")

    assert run["status"] == "running"
    assert run["tasks"] == []
    assert manager.get_active_run("session")["run_id"] == run["run_id"]

    interrupted = manager.interrupt_run(run["run_id"])

    assert interrupted["status"] == "interrupted"
    assert interrupted["reason"] == "user_cancelled"
    assert interrupted["error"] is None
    assert manager.get_active_run("session") is None

    next_run = manager.create_run("session", "workspace", "continue")
    assert next_run["run_id"] != run["run_id"]


def test_run_manager_rejects_overlapping_session_runs(tmp_path: Path) -> None:
    manager = RunManager(tmp_path)
    manager.create_run("session", "workspace", "first")

    try:
        manager.create_run("session", "workspace", "second")
    except RuntimeError as exc:
        assert "already active" in str(exc)
    else:
        raise AssertionError("Expected overlapping Run to be rejected")
