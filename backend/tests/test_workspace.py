from pathlib import Path

import pytest

from service.session_manager import SessionManager
from service.workspace_manager import WorkspaceManager
from tools import get_all_tools


def test_workspace_manager_scopes_tools_and_session_binding(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repo_a = root / "a"
    repo_b = root / "b"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)
    manager = WorkspaceManager(tmp_path / "state", root)
    workspace_a = manager.create_workspace(repo_a)
    workspace_b = manager.create_workspace(repo_b)
    registry = (tmp_path / "state" / "workspaces.json").read_text(encoding="utf-8")
    assert str(root) not in registry

    sessions = SessionManager(tmp_path / "state")
    session_a = sessions.create_session("A", workspace_a.workspace_id)
    session_b = sessions.create_session("B", workspace_b.workspace_id)
    assert session_a["workspace_id"] != session_b["workspace_id"]
    with pytest.raises(ValueError, match="cannot switch"):
        sessions.bind_workspace(session_a["id"], workspace_b.workspace_id)

    tools_a = {tool.name: tool for tool in get_all_tools(repo_a)}
    tools_b = {tool.name: tool for tool in get_all_tools(repo_b)}
    assert "Wrote" in tools_a["write_file"].invoke({"path": "only-a.txt", "content": "A"})
    assert tools_b["read_file"].invoke({"path": "only-a.txt"}) == "Read failed: file does not exist."


def test_workspace_rejects_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    manager = WorkspaceManager(tmp_path / "state", root)
    with pytest.raises(ValueError, match="WORKSPACE_ROOT"):
        manager.create_workspace(outside)
