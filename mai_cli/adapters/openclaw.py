"""OpenClaw host adapter helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

DEFAULT_SKILL_ROOT = Path.home() / ".openclaw" / "workspace" / "skills" / "mai"


def resolve_project_root(project_root: str | Path | None = None) -> Path:
    explicit = project_root or os.environ.get("MAI_ROOT")
    if explicit:
        return Path(explicit).expanduser()

    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "scripts" / "mai.py").exists():
        return repo_root
    return DEFAULT_SKILL_ROOT


def build_mai_command(
    subcommand_args: Iterable[object] = (),
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    command = ["python3", str(resolve_project_root(project_root) / "scripts" / "mai.py")]
    if db_path is not None:
        command.extend(["--db", str(Path(db_path).expanduser())])
    command.extend(str(arg) for arg in subcommand_args)
    return command


def merchant_agent_command(
    merchant_id: str,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    once: bool = False,
    interval: float | None = None,
) -> list[str]:
    args: list[object] = ["agent", "run", "--merchant", merchant_id, "--format", "json"]
    if once:
        args.append("--once")
    if interval is not None:
        args.extend(["--interval", interval])
    return build_mai_command(args, db_path=db_path, project_root=project_root)


__all__ = ["DEFAULT_SKILL_ROOT", "build_mai_command", "merchant_agent_command", "resolve_project_root"]
