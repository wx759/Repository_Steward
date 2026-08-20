from __future__ import annotations

import asyncio
import platform
import sys
import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from service.prompt_builder import SYSTEM_COMPONENTS, build_system_prompt
from service.session_manager import SessionManager
from graph.agent import AgentManager
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
    restored = reloaded.load_session_for_agent(session["id"])
    assert [type(message) for message in restored] == [HumanMessage, AIMessage]
    assert [message.content for message in restored] == ["read auth.py", "auth.py loaded"]


def test_session_round_trips_standard_tool_messages_and_projects_ui_history(
    tmp_path: Path,
) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create_session("tool test")
    manager.append_agent_messages(
        session["id"],
        [
            HumanMessage(content="read README.md"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="README content",
                tool_call_id="call-1",
                name="read_file",
            ),
            AIMessage(content="README loaded"),
        ],
    )

    raw_messages = manager.load_session(session["id"])
    assert [message["type"] for message in raw_messages] == [
        "human",
        "ai",
        "tool",
        "ai",
    ]
    assert raw_messages[1]["tool_calls"][0]["id"] == "call-1"
    assert raw_messages[2]["tool_call_id"] == "call-1"

    restored = manager.load_session_for_agent(session["id"])
    assert [type(message) for message in restored] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert restored[1].tool_calls[0]["id"] == restored[2].tool_call_id
    assert restored[2].content == "README content"

    assert manager.get_history(session["id"])["messages"] == [
        {"role": "user", "content": "read README.md"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "tool": "read_file",
                    "input": '{"path": "README.md"}',
                    "output": "README content",
                }
            ],
        },
        {"role": "assistant", "content": "README loaded"},
    ]
    summaries = manager.list_sessions()
    assert summaries[0]["message_count"] == 2


def test_session_preserves_incomplete_ai_recovery_status(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create_session()
    incomplete = AIMessage(
        content="partial answer",
        additional_kwargs={
            "recovery": {
                "status": "incomplete",
                "reason": "max_output_tokens",
                "continuation_count": 3,
                "generated_by": "model",
            }
        },
    )

    manager.append_agent_messages(session["id"], [incomplete])

    record = manager.load_session_record(session["id"])["messages"][0]
    assert record["status"] == "incomplete"
    assert record["finish_reason"] == "max_output_tokens"
    restored = manager.load_session_for_agent(session["id"])[0]
    assert restored.additional_kwargs["recovery"]["status"] == "incomplete"
    display = manager.get_history(session["id"])["messages"][0]
    assert display["status"] == "incomplete"


def test_legacy_ui_session_messages_are_migrated_on_read(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create_session("legacy")
    path = tmp_path / "sessions" / f"{session['id']}.json"
    record = manager.load_session_record(session["id"])
    record.pop("schema_version", None)
    record["messages"] = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": "done",
            "tool_calls": [
                {"tool": "terminal", "input": '{"command":"pwd"}', "output": "repo"}
            ],
        },
    ]
    path.write_text(json.dumps(record), encoding="utf-8")

    migrated = manager.load_session_record(session["id"])
    assert migrated["schema_version"] == 3
    assert [message["type"] for message in migrated["messages"]] == [
        "human",
        "ai",
        "tool",
    ]
    assert migrated["messages"][1]["tool_calls"][0]["id"] == migrated["messages"][2][
        "tool_call_id"
    ]


def test_prompt_is_composed_from_workspace_and_skill_files(tmp_path: Path) -> None:
    _write_prompt_components(tmp_path)
    prompt = build_system_prompt(tmp_path)

    for label, relative_path in SYSTEM_COMPONENTS:
        assert f"<!-- {label} -->" in prompt
        assert f"content:{relative_path}" in prompt
    assert "not files or directories in the user's selected repository" in prompt
    assert "must be grounded only in results from tools" in prompt


def test_first_tool_set_is_minimal(tmp_path: Path) -> None:
    assert [tool.name for tool in get_all_tools(tmp_path)] == [
        "read_file",
        "write_file",
        "terminal",
    ]


def test_system_prompt_declares_single_steward(tmp_path: Path) -> None:
    from service.prompt_builder import build_system_prompt

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "STEWARD.md").write_text("single long-lived Steward Agent", encoding="utf-8")
    assert "single long-lived Steward Agent" in build_system_prompt(tmp_path)


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

    graph_messages = graph.payload["messages"]
    assert [type(message) for message in graph_messages] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert [message.content for message in graph_messages] == [
        "read auth.py",
        "auth.py loaded",
        "what does it do?",
    ]
    assert events[0]["type"] == "done"
    assert events[0]["content"] == ""
    assert len(events[0]["_session_messages"]) == 1
    assert isinstance(events[0]["_session_messages"][0], HumanMessage)


def test_agent_done_event_contains_standard_messages_from_model_and_tools(
    tmp_path: Path,
) -> None:
    tool_request = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": "read_file",
                "args": {"path": "README.md"},
                "type": "tool_call",
            }
        ],
    )
    tool_result = ToolMessage(
        content="README content",
        tool_call_id="call-1",
        name="read_file",
    )
    final_answer = AIMessage(content="README loaded")

    class FakeGraph:
        async def astream(self, _payload, **_kwargs):
            yield "updates", {"model": {"messages": [tool_request]}}
            yield "updates", {"tools": {"messages": [tool_result]}}
            yield "updates", {"model": {"messages": [final_answer]}}

    manager = AgentManager()
    manager.base_dir = tmp_path
    manager.tools = []
    manager._agent_graph = FakeGraph()
    manager._agent_graph_tools_id = id(manager.tools)

    events = asyncio.run(
        _collect_agent_events(manager, "read README.md", [])
    )
    done = events[-1]

    assert done["content"] == "README loaded"
    persisted = done["_session_messages"]
    assert [type(message) for message in persisted] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert persisted[1].tool_calls[0]["id"] == persisted[2].tool_call_id


def test_agent_forwards_recovery_event_and_incomplete_done_status(tmp_path: Path) -> None:
    incomplete = AIMessage(
        content="partial",
        additional_kwargs={
            "recovery": {
                "status": "incomplete",
                "reason": "max_output_tokens",
                "continuation_count": 3,
            }
        },
    )

    class FakeGraph:
        async def astream(self, _payload, **_kwargs):
            yield "custom", {
                "type": "recovery",
                "reason": "overloaded",
                "message": "模型服务当前过载，正在重试。",
            }
            yield "updates", {"model": {"messages": [incomplete]}}

    manager = AgentManager()
    manager.base_dir = tmp_path
    manager.tools = []
    manager._agent_graph = FakeGraph()
    manager._agent_graph_tools_id = id(manager.tools)

    events = asyncio.run(_collect_agent_events(manager, "question", []))

    assert events[0]["type"] == "recovery"
    assert events[-1]["status"] == "incomplete"
    assert events[-1]["reason"] == "max_output_tokens"
    assert events[-1]["continuation_count"] == 3


async def _collect_agent_events(
    manager: AgentManager,
    message: str,
    history: list,
) -> list[dict]:
    return [event async for event in manager.astream(message, history)]


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
