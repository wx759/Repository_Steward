from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str
    root_path: str
    relative_path: str
    created_at: float


class WorkspaceManager:
    """Persist and resolve repository workspaces below one configured root."""

    def __init__(self, state_dir: Path, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = state_dir / "workspaces.json"
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self.registry_path.write_text("[]\n", encoding="utf-8")
        else:
            # Rewrite legacy registries so host absolute paths are removed.
            self._write(self._read())

    def _read(self) -> list[Workspace]:
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return [
            Workspace(
                workspace_id=str(item["workspace_id"]),
                name=str(item["name"]),
                root_path=str((self.workspace_root / str(item["relative_path"])).resolve()),
                relative_path=str(item["relative_path"]),
                created_at=float(item["created_at"]),
            )
            for item in raw
        ]

    def _write(self, workspaces: list[Workspace]) -> None:
        self.registry_path.write_text(
            json.dumps(
                [
                    {
                        "workspace_id": item.workspace_id,
                        "name": item.name,
                        "relative_path": item.relative_path,
                        "created_at": item.created_at,
                    }
                    for item in workspaces
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _relative_path(self, root_path: Path) -> str:
        resolved = root_path.expanduser().resolve()
        if not resolved.exists():
            raise ValueError("Workspace path does not exist")
        if not resolved.is_dir():
            raise ValueError("Workspace path is not a directory")
        try:
            relative = resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("Workspace path must be inside WORKSPACE_ROOT") from exc
        return relative.as_posix() or "."

    def create_workspace(
        self,
        root_path: str | Path,
        *,
        name: str | None = None,
        require_git: bool = False,
    ) -> Workspace:
        resolved = Path(root_path).expanduser().resolve()
        relative_path = self._relative_path(resolved)
        if require_git and not (resolved / ".git").exists():
            raise ValueError("Workspace is not a Git repository")
        workspaces = self._read()
        for workspace in workspaces:
            if workspace.relative_path == relative_path:
                return workspace
        workspace = Workspace(
            workspace_id=uuid.uuid4().hex,
            name=(name or resolved.name or "workspace").strip(),
            root_path=str(resolved),
            relative_path=relative_path,
            created_at=time.time(),
        )
        workspaces.append(workspace)
        self._write(workspaces)
        return workspace

    def get_workspace(self, workspace_id: str) -> Workspace:
        for workspace in self._read():
            if workspace.workspace_id == workspace_id:
                resolved = self.resolve_root(workspace)
                return Workspace(
                    workspace_id=workspace.workspace_id,
                    name=workspace.name,
                    root_path=str(resolved),
                    relative_path=workspace.relative_path,
                    created_at=workspace.created_at,
                )
        raise KeyError(f"Workspace not found: {workspace_id}")

    def resolve_root(self, workspace: Workspace) -> Path:
        candidate = (self.workspace_root / workspace.relative_path).resolve()
        self._relative_path(candidate)
        return candidate

    def list_workspaces(self) -> list[Workspace]:
        return [self.get_workspace(item.workspace_id) for item in self._read()]
