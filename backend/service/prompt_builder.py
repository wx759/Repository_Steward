from __future__ import annotations

from pathlib import Path

from config import get_settings


SYSTEM_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("Skills Snapshot", "skills/SKILLS_SNAPSHOT.md"),
    ("Soul", "workspace/SOUL.md"),
    ("Identity", "workspace/IDENTITY.md"),
    ("User Profile", "workspace/USER.md"),
    ("Agents Guide", "workspace/AGENTS.md"),
    ("Steward Role", "workspace/STEWARD.md"),
)

SYSTEM_BOUNDARY = """<!-- Context Boundary -->
The system components below are internal Repository Steward instructions and capabilities.
They are not files or directories in the user's selected repository.
Never include paths such as `skills/`, `workspace/`, `.qwen/`, or any other internal
configuration path in a repository inventory unless a repository tool actually returned
that exact path. Every claim about repository files, directories, Git state, or project
structure must be grounded only in results from tools executed against the selected
repository. If tool output and prior knowledge conflict, trust the tool output."""


def _read_component(base_dir: Path, relative_path: str, limit: int) -> str:
    path = base_dir / relative_path
    if not path.exists():
        return f"[missing component: {relative_path}]"
    text = path.read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def build_system_prompt(base_dir: Path) -> str:
    limit = get_settings().component_char_limit
    components = (
        f"<!-- {label} -->\n{_read_component(base_dir, relative_path, limit)}"
        for label, relative_path in SYSTEM_COMPONENTS
    )
    return "\n\n".join((SYSTEM_BOUNDARY, *components))
