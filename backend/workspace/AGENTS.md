# Agent Guide

## Core principles

1. Inspect the repository before making changes.
2. Prefer the smallest change that satisfies the request.
3. Keep tool calls and conclusions transparent.
4. Preserve unrelated user changes.
5. Verify code changes with focused tests or checks.

## Tools

- `read_file`: Read a UTF-8 text file under the backend workspace.
- `terminal`: Run a local shell command from the backend workspace. It can also be used to edit files when requested.

## Skills

Available skills are listed in `skills/SKILLS_SNAPSHOT.md`. When a relevant skill exists, read its `SKILL.md` with `read_file` before following its workflow.
