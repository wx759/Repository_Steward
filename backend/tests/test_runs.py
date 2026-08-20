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
