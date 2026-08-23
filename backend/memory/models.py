from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MemoryType = Literal["user", "project", "feedback", "reference"]
MEMORY_TYPES: frozenset[str] = frozenset(
    {"user", "project", "feedback", "reference"}
)
MemoryStatus = Literal["active", "superseded", "archived"]
MEMORY_STATUSES: frozenset[str] = frozenset(
    {"active", "superseded", "archived"}
)


@dataclass(frozen=True)
class MemoryDraft:
    name: str
    type: MemoryType
    description: str
    body: str
    workspace_id: str | None = None
    evidence_quote: str = ""


@dataclass(frozen=True)
class MemoryMetadata:
    id: str
    name: str
    type: MemoryType
    description: str
    status: MemoryStatus
    created_at: str
    updated_at: str
    workspace_id: str | None = None
    evidence_quote: str = ""


@dataclass(frozen=True)
class MemoryRecord(MemoryMetadata):
    body: str = ""
