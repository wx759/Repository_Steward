from __future__ import annotations

import json
from pathlib import Path

from langchain.agents import create_agent

from delegation.models import TaskSpec, WorkerResult
from delegation.parsing import parse_model
from delegation.permissions import PermissionScope, worker_tools
from middleware import AgentRunContext


ROLE_PROMPTS = {
    "backend": "You are a backend specialist. Focus on server code, data, APIs, and migrations.",
    "frontend": "You are a frontend specialist. Focus on UI, client state, accessibility, and types.",
    "test": "You are a test specialist. Focus on reproducible verification and tests.",
    "general": "You are a repository implementation specialist for cross-cutting tasks.",
}

RESULT_INSTRUCTION = """
Work only on the supplied task and PermissionScope. Use repository tools as needed. The
run_command tool accepts structured command identifiers, never shell strings. Do not ask the user
questions and do not claim permissions beyond the supplied task.
Finish by returning only one JSON object with keys: status ('success' or 'failed'), summary,
changed_files, tests_run, issues. Do not wrap it in prose.
"""


class WorkerFactory:
    def __init__(self, model, *, middleware: list | None = None) -> None:
        self.model = model
        self.middleware = list(middleware or [])

    async def run(
        self,
        workspace_root: Path,
        task: TaskSpec,
        *,
        feedback: str = "",
    ) -> WorkerResult:
        prompt = f"{ROLE_PROMPTS[task.role]}\n{RESULT_INSTRUCTION}"
        scope = PermissionScope.for_task(workspace_root, task)
        agent = create_agent(
            model=self.model,
            tools=worker_tools(scope),
            system_prompt=prompt,
            middleware=self.middleware,
            context_schema=AgentRunContext,
        )
        payload = task.model_dump()
        if feedback:
            payload["review_feedback"] = feedback
        response = await agent.ainvoke(
            {"messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]},
            context=AgentRunContext(session_id=f"worker-{task.role}"),
        )
        messages = response.get("messages", [])
        content = getattr(messages[-1], "content", "") if messages else ""
        try:
            return parse_model(WorkerResult, content)
        except Exception as exc:
            return WorkerResult(
                status="failed",
                summary="Worker did not return a valid structured result.",
                issues=[type(exc).__name__],
            )
