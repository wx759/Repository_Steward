from __future__ import annotations

import asyncio
import fnmatch
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Type

from langchain_core.callbacks.manager import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from config import get_settings
from delegation.models import CommandName, TaskSpec, WorkerRole
from tools import ReadFileTool, WriteFileTool


REPOSITORY_PATHS = ("**",)
DENIED_PATHS = (
    ".git",
    ".git/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    ".task_outputs/**",
)


@dataclass(frozen=True)
class RolePolicy:
    readable_paths: tuple[str, ...]
    writable_paths: tuple[str, ...]
    commands: frozenset[CommandName]


ROLE_POLICIES: dict[WorkerRole, RolePolicy] = {
    "backend": RolePolicy(
        ("**",),
        ("backend/**",),
        frozenset({"pytest", "ruff_check", "mypy"}),
    ),
    "frontend": RolePolicy(
        ("**",),
        ("frontend/**",),
        frozenset({"npm_test", "npm_lint", "npm_build"}),
    ),
    "test": RolePolicy(
        ("**",),
        ("tests/**", "backend/tests/**", "frontend/**/*.test.*"),
        frozenset({"pytest", "ruff_check", "mypy", "npm_test", "npm_lint"}),
    ),
    "general": RolePolicy(
        ("**",),
        ("**",),
        frozenset(
            {"pytest", "ruff_check", "mypy", "npm_test", "npm_lint", "npm_build"}
        ),
    ),
}

REVIEW_COMMANDS: frozenset[CommandName] = frozenset(
    {"pytest", "ruff_check", "mypy", "npm_test", "npm_lint", "npm_build"}
)


def normalize_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or ".." in candidate.parts
    ):
        return None
    return candidate.as_posix()


def path_matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    normalized = normalize_path(path)
    if normalized is None:
        return False
    return any(
        pattern == "**"
        or fnmatch.fnmatch(normalized, pattern)
        or (pattern.endswith("/**") and normalized == pattern[:-3])
        for pattern in patterns
    )


@dataclass(frozen=True)
class PermissionScope:
    """Capabilities after repository, role, and task policies are intersected."""

    root_dir: Path
    role: WorkerRole
    repository_readable_paths: tuple[str, ...]
    repository_writable_paths: tuple[str, ...]
    role_readable_paths: tuple[str, ...]
    role_writable_paths: tuple[str, ...]
    task_writable_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    allowed_commands: frozenset[CommandName]

    @classmethod
    def for_task(cls, root_dir: Path, task: TaskSpec) -> PermissionScope:
        role_policy = ROLE_POLICIES[task.role]
        task_paths = tuple(task.allowed_paths or role_policy.writable_paths)
        if task.role == "general" and not task.allowed_paths:
            task_paths = ()
        return cls(
            root_dir=root_dir.resolve(),
            role=task.role,
            repository_readable_paths=REPOSITORY_PATHS,
            repository_writable_paths=REPOSITORY_PATHS,
            role_readable_paths=role_policy.readable_paths,
            role_writable_paths=role_policy.writable_paths,
            task_writable_paths=task_paths,
            denied_paths=DENIED_PATHS,
            allowed_commands=role_policy.commands.intersection(task.allowed_commands),
        )

    @classmethod
    def for_reviewer(cls, root_dir: Path) -> PermissionScope:
        return cls(
            root_dir=root_dir.resolve(),
            role="general",
            repository_readable_paths=REPOSITORY_PATHS,
            repository_writable_paths=(),
            role_readable_paths=("**",),
            role_writable_paths=(),
            task_writable_paths=(),
            denied_paths=DENIED_PATHS,
            allowed_commands=REVIEW_COMMANDS,
        )

    def can_read(self, path: str) -> bool:
        return (
            not path_matches(path, self.denied_paths)
            and path_matches(path, self.repository_readable_paths)
            and path_matches(path, self.role_readable_paths)
        )

    def can_write(self, path: str) -> bool:
        return (
            bool(self.task_writable_paths)
            and not path_matches(path, self.denied_paths)
            and path_matches(path, self.repository_writable_paths)
            and path_matches(path, self.role_writable_paths)
            and path_matches(path, self.task_writable_paths)
        )

    def can_run(self, command: CommandName) -> bool:
        return command in self.allowed_commands


class ScopedReadFileTool(ReadFileTool):
    _scope: PermissionScope = PrivateAttr()

    def __init__(self, scope: PermissionScope, **kwargs) -> None:
        super().__init__(scope.root_dir, **kwargs)
        self._scope = scope

    def _run(self, path: str, run_manager=None) -> str:
        if not self._scope.can_read(path):
            return f"Read denied by PermissionScope: {path}"
        return super()._run(path, run_manager)


class ScopedWriteFileTool(WriteFileTool):
    _scope: PermissionScope = PrivateAttr()

    def __init__(self, scope: PermissionScope, **kwargs) -> None:
        super().__init__(scope.root_dir, **kwargs)
        self._scope = scope

    def _run(self, path: str, content: str, run_manager=None) -> str:
        if not self._scope.can_write(path):
            return f"Write denied by PermissionScope: {path}"
        return super()._run(path, content, run_manager)


class StructuredCommandInput(BaseModel):
    command: CommandName = Field(..., description="One pre-approved command identifier")
    targets: list[str] = Field(
        default_factory=list,
        max_length=30,
        description="Optional repository-relative paths; shell flags are forbidden",
    )


COMMAND_ARGV: dict[CommandName, tuple[str, ...]] = {
    "pytest": ("python", "-m", "pytest"),
    "ruff_check": ("ruff", "check"),
    "mypy": ("mypy",),
    "npm_test": ("npm", "test"),
    "npm_lint": ("npm", "run", "lint"),
    "npm_build": ("npm", "run", "build"),
}


class StructuredCommandTool(BaseTool):
    name: str = "run_command"
    description: str = (
        "Run one structured, pre-approved verification command without a shell. "
        "Available commands depend on the current PermissionScope."
    )
    args_schema: Type[BaseModel] = StructuredCommandInput
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _scope: PermissionScope = PrivateAttr()

    def __init__(self, scope: PermissionScope, **kwargs) -> None:
        super().__init__(**kwargs)
        self._scope = scope

    def _validated_targets(self, command: CommandName, targets: list[str]) -> list[str] | None:
        if command.startswith("npm_") and targets:
            return None
        validated: list[str] = []
        for target in targets:
            file_part = target.split("::", 1)[0]
            if target.startswith("-") or not self._scope.can_read(file_part):
                return None
            validated.append(target.replace("\\", "/"))
        return validated

    def _run(
        self,
        command: CommandName,
        targets: list[str] | None = None,
        run_manager: CallbackManagerForToolRun | None = None,
    ) -> str:
        if not self._scope.can_run(command):
            return f"Command denied by PermissionScope: {command}"
        validated_targets = self._validated_targets(command, list(targets or []))
        if validated_targets is None:
            return "Command denied: targets must be repository-relative paths without flags"
        cwd = self._scope.root_dir
        if command.startswith("npm_") and (cwd / "frontend" / "package.json").exists():
            cwd = cwd / "frontend"
        environment = os.environ.copy()
        environment.update({"CI": "1", "GIT_TERMINAL_PROMPT": "0"})
        try:
            completed = subprocess.run(
                [*COMMAND_ARGV[command], *validated_targets],
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                timeout=get_settings().terminal_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Command failed: {type(exc).__name__}"
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        return f"exit_code={completed.returncode}\n{output or '[no output]'}"

    async def _arun(
        self,
        command: CommandName,
        targets: list[str] | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        return await asyncio.to_thread(self._run, command, targets, None)


def worker_tools(scope: PermissionScope) -> list[BaseTool]:
    return [
        ScopedReadFileTool(scope),
        ScopedWriteFileTool(scope),
        StructuredCommandTool(scope),
    ]


def reviewer_tools(root_dir: Path) -> list[BaseTool]:
    scope = PermissionScope.for_reviewer(root_dir)
    return [ScopedReadFileTool(scope), StructuredCommandTool(scope)]
