from __future__ import annotations

import json
from pathlib import Path
from typing import Type

from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from pydantic import BaseModel, ConfigDict, PrivateAttr

from delegation.models import TaskSpec
from delegation.service import DelegationService
from service.run_manager import RunManager


class DelegateTaskTool(BaseTool):
    name: str = "delegate_task"
    description: str = (
        "Delegate one complex, bounded repository task to a temporary specialized worker. "
        "Calls are strictly serial and return only a structured WorkerResult."
    )
    args_schema: Type[BaseModel] = TaskSpec
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _workspace_root: Path = PrivateAttr()
    _service: DelegationService = PrivateAttr()
    _run_manager: RunManager = PrivateAttr()
    _session_id: str = PrivateAttr()
    _workspace_id: str = PrivateAttr()

    def __init__(
        self,
        workspace_root: Path,
        service: DelegationService,
        run_manager: RunManager,
        session_id: str,
        workspace_id: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._workspace_root = workspace_root
        self._service = service
        self._run_manager = run_manager
        self._session_id = session_id
        self._workspace_id = workspace_id

    def _run(self, **_kwargs) -> str:
        return "delegate_task is async-only"

    async def _arun(self, **kwargs) -> str:
        task = TaskSpec(**kwargs)
        run = self._run_manager.ensure_run(
            self._session_id,
            self._workspace_id,
            task.description,
        )
        task_id = self._run_manager.add_task(run["run_id"], task)
        try:
            get_stream_writer()({"type": "run", "run_id": run["run_id"], "status": "running"})
        except RuntimeError:
            pass
        result = await self._service.delegate(self._workspace_root, task)
        self._run_manager.finish_task(run["run_id"], task_id, result)
        try:
            get_stream_writer()(
                {"type": "run", "run_id": run["run_id"], "status": result.status}
            )
        except RuntimeError:
            pass
        return json.dumps(result.model_dump(), ensure_ascii=False)
