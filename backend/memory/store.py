from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, cast
from uuid import uuid4

from .models import (
    MEMORY_STATUSES,
    MEMORY_TYPES,
    MemoryDraft,
    MemoryMetadata,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """SQLite-backed persistence boundary for long-term memories."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL CHECK (
                                    type IN ('user', 'project', 'feedback', 'reference')
                    ),
                    description TEXT NOT NULL,
                    body        TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'active' CHECK (
                                    status IN ('active', 'superseded', 'archived')
                                ),
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    workspace_id TEXT NULL,
                    evidence_quote TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "status" not in columns:
                connection.execute(
                    """
                    ALTER TABLE memories
                    ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (
                        status IN ('active', 'superseded', 'archived')
                    )
                    """
                )
            if "workspace_id" not in columns:
                connection.execute("ALTER TABLE memories ADD COLUMN workspace_id TEXT NULL")
            if "evidence_quote" not in columns:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN evidence_quote TEXT NOT NULL DEFAULT ''"
                )
            connection.executescript(
                """
                DROP INDEX IF EXISTS ux_memories_name_nocase;
                DROP INDEX IF EXISTS ux_memories_active_name_nocase;

                CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_active_name_scope_nocase
                ON memories(name COLLATE NOCASE, COALESCE(workspace_id, ''))
                WHERE status = 'active';

                CREATE INDEX IF NOT EXISTS ix_memories_updated_at
                ON memories(updated_at DESC);

                CREATE INDEX IF NOT EXISTS ix_memories_status_updated_at
                ON memories(status, updated_at DESC);

                CREATE INDEX IF NOT EXISTS ix_memories_workspace_status
                ON memories(workspace_id, status, updated_at DESC);
                """
            )

    @staticmethod
    def _clean_draft(draft: MemoryDraft) -> MemoryDraft:
        name = draft.name.strip()
        memory_type = str(draft.type).strip().lower()
        description = draft.description.strip()
        body = draft.body.strip()
        if not name or not description or not body:
            raise ValueError("Memory name, description, and body must not be empty")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Unsupported memory type: {memory_type}")
        return MemoryDraft(
            name=name,
            type=cast(MemoryType, memory_type),
            description=description,
            body=body,
            workspace_id=(
                None
                if memory_type == "user"
                else (draft.workspace_id.strip() if draft.workspace_id else None)
            ),
            evidence_quote=draft.evidence_quote.strip(),
        )

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> MemoryMetadata:
        return MemoryMetadata(
            id=str(row["id"]),
            name=str(row["name"]),
            type=cast(MemoryType, str(row["type"])),
            description=str(row["description"]),
            status=cast(MemoryStatus, str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            workspace_id=(str(row["workspace_id"]) if row["workspace_id"] else None),
            evidence_quote=str(row["evidence_quote"] or ""),
        )

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> MemoryRecord:
        metadata = cls._metadata_from_row(row)
        return MemoryRecord(
            id=metadata.id,
            name=metadata.name,
            type=metadata.type,
            description=metadata.description,
            status=metadata.status,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
            workspace_id=metadata.workspace_id,
            evidence_quote=metadata.evidence_quote,
            body=str(row["body"]),
        )

    def list_metadata(
        self,
        *,
        status: MemoryStatus | None = "active",
    ) -> list[MemoryMetadata]:
        if status is not None and status not in MEMORY_STATUSES:
            raise ValueError(f"Unsupported memory status: {status}")
        where_clause = "WHERE status = ?" if status is not None else ""
        parameters = (status,) if status is not None else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, name, type, description, status, created_at, updated_at,
                       workspace_id, evidence_quote
                FROM memories
                {where_clause}
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
                """,
                parameters,
            ).fetchall()
        return [self._metadata_from_row(row) for row in rows]

    def list_scoped_metadata(self, workspace_id: str) -> list[MemoryMetadata]:
        """Return global user memories plus memories owned by this repository."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, type, description, status, created_at, updated_at,
                       workspace_id, evidence_quote
                FROM memories
                WHERE status = 'active'
                  AND ((type = 'user' AND workspace_id IS NULL) OR workspace_id = ?)
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._metadata_from_row(row) for row in rows]

    def get_memories(self, ids: Sequence[str]) -> list[MemoryRecord]:
        ordered_ids = list(dict.fromkeys(str(memory_id) for memory_id in ids if memory_id))
        if not ordered_ids:
            return []
        placeholders = ",".join("?" for _ in ordered_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memories
                WHERE id IN ({placeholders}) AND status = 'active'
                """,
                ordered_ids,
            ).fetchall()
        records = {str(row["id"]): self._record_from_row(row) for row in rows}
        return [records[memory_id] for memory_id in ordered_ids if memory_id in records]

    def list_scoped_memories(
        self,
        workspace_id: str,
        *,
        status: MemoryStatus | None = None,
    ) -> list[MemoryRecord]:
        """Return reviewable memory bodies visible to one repository."""
        if status is not None and status not in MEMORY_STATUSES:
            raise ValueError(f"Unsupported memory status: {status}")
        status_clause = "AND status = ?" if status is not None else ""
        parameters: tuple[str, ...] = (
            (workspace_id, status) if status is not None else (workspace_id,)
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memories
                WHERE ((type = 'user' AND workspace_id IS NULL) OR workspace_id = ?)
                {status_clause}
                ORDER BY updated_at DESC, name COLLATE NOCASE ASC
                """,
                parameters,
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def save_memory(self, draft: MemoryDraft) -> MemoryRecord:
        cleaned = self._clean_draft(draft)
        memory_id = uuid4().hex
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, name, type, description, body, status, created_at, updated_at,
                        workspace_id, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        cleaned.name,
                        cleaned.type,
                        cleaned.description,
                        cleaned.body,
                        now,
                        now,
                        cleaned.workspace_id,
                        cleaned.evidence_quote,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Memory name already exists: {cleaned.name}") from exc
        return MemoryRecord(
            id=memory_id,
            name=cleaned.name,
            type=cleaned.type,
            description=cleaned.description,
            status="active",
            body=cleaned.body,
            created_at=now,
            updated_at=now,
            workspace_id=cleaned.workspace_id,
            evidence_quote=cleaned.evidence_quote,
        )

    def update_memory(self, memory_id: str, draft: MemoryDraft) -> MemoryRecord:
        cleaned = self._clean_draft(draft)
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT created_at, status FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
                if existing is None:
                    raise KeyError(f"Memory not found: {memory_id}")
                connection.execute(
                    """
                    UPDATE memories
                    SET name = ?, type = ?, description = ?, body = ?, updated_at = ?,
                        workspace_id = ?, evidence_quote = ?
                    WHERE id = ?
                    """,
                    (
                        cleaned.name,
                        cleaned.type,
                        cleaned.description,
                        cleaned.body,
                        now,
                        cleaned.workspace_id,
                        cleaned.evidence_quote,
                        memory_id,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Memory name already exists: {cleaned.name}") from exc
        return MemoryRecord(
            id=memory_id,
            name=cleaned.name,
            type=cleaned.type,
            description=cleaned.description,
            status=cast(MemoryStatus, str(existing["status"])),
            body=cleaned.body,
            created_at=str(existing["created_at"]),
            updated_at=now,
            workspace_id=cleaned.workspace_id,
            evidence_quote=cleaned.evidence_quote,
        )

    def supersede_memory(
        self,
        memory_id: str,
        replacement: MemoryDraft,
    ) -> MemoryRecord:
        """Deactivate one active memory and insert its active replacement atomically."""
        cleaned = self._clean_draft(replacement)
        replacement_id = uuid4().hex
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded', updated_at = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (now, memory_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Active memory not found: {memory_id}")
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, name, type, description, body, status, created_at, updated_at,
                        workspace_id, evidence_quote
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        replacement_id,
                        cleaned.name,
                        cleaned.type,
                        cleaned.description,
                        cleaned.body,
                        now,
                        now,
                        cleaned.workspace_id,
                        cleaned.evidence_quote,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Active memory name already exists: {cleaned.name}"
            ) from exc
        return MemoryRecord(
            id=replacement_id,
            name=cleaned.name,
            type=cleaned.type,
            description=cleaned.description,
            status="active",
            body=cleaned.body,
            created_at=now,
            updated_at=now,
            workspace_id=cleaned.workspace_id,
            evidence_quote=cleaned.evidence_quote,
        )

    def archive_memory(self, memory_id: str) -> bool:
        """Stop recalling an active memory without deleting its stored content."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE memories
                SET status = 'archived', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, memory_id),
            )
            connection.commit()
        return cursor.rowcount == 1

    def delete_memory(self, memory_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,),
            )
            connection.commit()
        return cursor.rowcount > 0
