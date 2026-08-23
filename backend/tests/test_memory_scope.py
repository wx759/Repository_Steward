from pathlib import Path

from memory import MemoryDraft, MemoryStore


def test_memory_catalog_is_scoped_to_user_and_current_workspace(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    user = store.save_memory(
        MemoryDraft("language", "user", "Preferred language", "Use Chinese", workspace_id="ignored")
    )
    project_a = store.save_memory(
        MemoryDraft("stack-a", "project", "A stack", "FastAPI", workspace_id="a")
    )
    project_b = store.save_memory(
        MemoryDraft("stack-b", "project", "B stack", "Django", workspace_id="b")
    )

    scoped_a = {item.id for item in store.list_scoped_metadata("a")}
    scoped_b = {item.id for item in store.list_scoped_metadata("b")}
    assert scoped_a == {user.id, project_a.id}
    assert scoped_b == {user.id, project_b.id}
    assert user.workspace_id is None


def test_memory_review_keeps_evidence_and_hides_other_workspaces(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    user = store.save_memory(
        MemoryDraft(
            "language",
            "user",
            "Preferred language",
            "Use Chinese",
            evidence_quote="以后都用中文回答",
        )
    )
    project_a = store.save_memory(
        MemoryDraft(
            "theme",
            "project",
            "Repository theme",
            "Use a light theme",
            workspace_id="a",
            evidence_quote="这个项目使用浅色主题",
        )
    )
    project_b = store.save_memory(
        MemoryDraft(
            "theme",
            "project",
            "Repository theme",
            "Use a dark theme",
            workspace_id="b",
            evidence_quote="另一个项目使用深色主题",
        )
    )
    assert store.archive_memory(project_a.id) is True

    reviewed_a = store.list_scoped_memories("a")
    assert [item.id for item in reviewed_a] == [project_a.id, user.id]
    assert reviewed_a[0].status == "archived"
    assert reviewed_a[0].evidence_quote == "这个项目使用浅色主题"
    assert project_b.id not in {item.id for item in reviewed_a}
    assert store.list_scoped_memories("a", status="active") == [user]
