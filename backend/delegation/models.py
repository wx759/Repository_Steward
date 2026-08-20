from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WorkerRole = Literal["backend", "frontend", "test", "general"]


class TaskSpec(BaseModel):
    role: WorkerRole
    description: str = Field(..., min_length=1, max_length=4_000)
    context: str = Field(default="", max_length=12_000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)


class WorkerResult(BaseModel):
    status: Literal["success", "failed"]
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""
