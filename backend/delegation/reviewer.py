from __future__ import annotations

import json
from pathlib import Path

from langchain.agents import create_agent

from delegation.models import ReviewResult, TaskSpec, WorkerResult
from delegation.parsing import parse_model
from delegation.permissions import reviewer_tools
from middleware import AgentRunContext


class Reviewer:
    def __init__(self, model, *, middleware: list | None = None) -> None:
        self.model = model
        self.middleware = list(middleware or [])

    async def review(
        self,
        workspace_root: Path,
        task: TaskSpec,
        result: WorkerResult,
        *,
        task_diff: str,
    ) -> ReviewResult:
        agent = create_agent(
            model=self.model,
            tools=reviewer_tools(workspace_root),
            system_prompt=(
                "You are a quality-only, read-only reviewer. PermissionScope and change-boundary "
                "validation have already been performed by deterministic code. Validate the "
                "acceptance criteria against WorkerResult, task diff, and checks. Never modify "
                "files. Return only JSON with approved, issues, feedback."
            ),
            middleware=self.middleware,
            context_schema=AgentRunContext,
        )
        review_input = {
            "task": task.model_dump(),
            "worker_result": result.model_dump(),
            "task_diff": task_diff,
        }
        response = await agent.ainvoke(
            {"messages": [{"role": "user", "content": json.dumps(review_input, ensure_ascii=False)}]},
            context=AgentRunContext(session_id="reviewer"),
        )
        messages = response.get("messages", [])
        content = getattr(messages[-1], "content", "") if messages else ""
        try:
            return parse_model(ReviewResult, content)
        except Exception as exc:
            return ReviewResult(
                approved=False,
                issues=[type(exc).__name__],
                feedback="Reviewer did not return valid structured JSON.",
            )
