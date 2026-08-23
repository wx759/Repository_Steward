from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitStateError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitStateError(detail or "Unable to inspect Git repository")
    return completed.stdout


def _fingerprint(path: Path) -> str | None:
    if path.is_symlink():
        return "symlink:" + str(path.readlink())
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class GitSnapshot:
    files: dict[str, str | None]


@dataclass(frozen=True)
class GitChanges:
    paths: tuple[str, ...]
    diff: str


class GitChangeTracker:
    """Observe changes relative to the exact file state before a worker starts."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        _git(self.root_dir, "rev-parse", "--show-toplevel")
        self.before = self.capture()

    def capture(self) -> GitSnapshot:
        tracked = _git(self.root_dir, "ls-files", "-z").split(b"\0")
        untracked = _git(
            self.root_dir,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        relative_paths = {
            item.decode("utf-8", errors="surrogateescape")
            for item in (*tracked, *untracked)
            if item
        }
        return GitSnapshot(
            {
                relative: _fingerprint(self.root_dir / relative)
                for relative in sorted(relative_paths)
            }
        )

    def changes(self) -> GitChanges:
        after = self.capture()
        paths = tuple(
            sorted(
                path
                for path in set(self.before.files) | set(after.files)
                if self.before.files.get(path) != after.files.get(path)
            )
        )
        if not paths:
            return GitChanges(paths=(), diff="[no task changes]")
        diff = _git(
            self.root_dir,
            "diff",
            "--no-ext-diff",
            "--binary",
            "--",
            *paths,
        ).decode("utf-8", errors="replace")
        untracked = [path for path in paths if self.before.files.get(path) is None]
        if untracked:
            diff += "\nUntracked files created by task:\n" + "\n".join(untracked)
        return GitChanges(paths=paths, diff=diff[-20_000:] or "[binary changes]")
