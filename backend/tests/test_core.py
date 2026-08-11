from __future__ import annotations

import asyncio
import platform
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from service.prompt_builder import SYSTEM_COMPONENTS, build_system_prompt
from service.session_manager import SessionManager
from graph.agent import AgentManager
from tools import get_all_tools
from tools.skills_scanner import refresh_snapshot, scan_skills


def _write_prompt_components(base_dir: Path) -> None:
    for _, relative_path in SYSTEM_COMPONENTS:
        path = base_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content:{relative_path}", encoding="utf-8")


def test_session_history_is_json_backed_and_agent_ready(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create_session("test")
    manager.save_message(session["id"], "user", "read auth.py")
    manager.save_message(session["id"], "assistant", "auth.py loaded")

    reloaded = SessionManager(tmp_path)
    assert reloaded.load_session_for_agent(session["id"]) == [
        {"role": "user", "content": "read auth.py"},
        {"role": "assistant", "content": "auth.py loaded"},
    ]


def test_prompt_is_composed_from_workspace_and_skill_files(tmp_path: Path) -> None:
    _write_prompt_components(tmp_path)
    prompt = build_system_prompt(tmp_path)

    for label, relative_path in SYSTEM_COMPONENTS:
        assert f"<!-- {label} -->" in prompt
        assert f"content:{relative_path}" in prompt


def test_first_tool_set_is_minimal(tmp_path: Path) -> None:
    assert [tool.name for tool in get_all_tools(tmp_path)] == ["read_file", "terminal"]


def test_core_tools_read_files_and_run_shell(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("hello", encoding="utf-8")
    tools = {tool.name: tool for tool in get_all_tools(tmp_path)}

    assert tools["read_file"].invoke({"path": "sample.txt"}) == "hello"
    command = "Write-Output shell-ok" if platform.system() == "Windows" else "printf shell-ok"
    assert "shell-ok" in tools["terminal"].invoke({"command": command})


def test_skill_snapshot_refreshes_from_skill_frontmatter(tmp_path: Path) -> None:
    skill_file = tmp_path / "skills" / "review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: code-review\ndescription: Review repository changes.\n---\n",
        encoding="utf-8",
    )

    records = scan_skills(tmp_path)
    snapshot = refresh_snapshot(tmp_path).read_text(encoding="utf-8")

    assert records[0].name == "code-review"
    assert 'name="code-review"' in snapshot
    assert 'path="skills/review/SKILL.md"' in snapshot


def test_agent_receives_saved_history_before_current_message(tmp_path: Path) -> None:
    class FakeGraph:
        payload: dict | None = None

        async def astream(self, payload, **_kwargs):
            self.payload = payload
            if False:
                yield None

    manager = AgentManager()
    manager.base_dir = tmp_path
    manager.tools = []
    graph = FakeGraph()
    manager._agent_graph = graph
    manager._agent_graph_tools_id = id(manager.tools)

    async def collect_events():
        return [
            event
            async for event in manager.astream(
                "what does it do?",
                [
                    {"role": "user", "content": "read auth.py"},
                    {"role": "assistant", "content": "auth.py loaded"},
                ],
            )
        ]

    events = asyncio.run(collect_events())

    assert graph.payload == {
        "messages": [
            {"role": "user", "content": "read auth.py"},
            {"role": "assistant", "content": "auth.py loaded"},
            {"role": "user", "content": "what does it do?"},
        ]
    }
    assert events == [{"type": "done", "content": ""}]


def test_fastapi_exposes_only_core_application_routes() -> None:
    from app import app

    paths = set(app.openapi()["paths"])
    assert {
        "/health",
        "/api/chat",
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/sessions/{session_id}/messages",
        "/api/sessions/{session_id}/history",
        "/api/sessions/{session_id}/generate-title",
        "/api/files",
        "/api/skills",
    }.issubset(paths)

