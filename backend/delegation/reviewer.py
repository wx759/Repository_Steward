from __future__ import annotations

import json
import subprocess
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

    @staticmethod
    def git_diff(workspace_root: Path) -> str:
        completed = subprocess.run(
            ["git", "diff", "--"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return (completed.stdout or completed.stderr)[-20_000:]

    async def review(self, workspace_root: Path, task: TaskSpec, result: WorkerResult) -> ReviewResult:
        agent = create_agent(
            model=self.model,
            tools=reviewer_tools(workspace_root),
            system_prompt=(
                "You are a read-only reviewer. Validate the TaskSpec acceptance criteria against "
                "the WorkerResult, git diff, and checks. Never modify files. Return only JSON with "
                "approved, issues, feedback."
            ),
            middleware=self.middleware,
            context_schema=AgentRunContext,
        )
        review_input = {
            "task": task.model_dump(),
            "worker_result": result.model_dump(),
            "git_diff": self.git_diff(workspace_root),
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
