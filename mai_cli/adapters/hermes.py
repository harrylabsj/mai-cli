"""Hermes host adapter helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

DEFAULT_SKILL_ROOT = Path.home() / ".hermes" / "skills" / "commerce" / "mai"


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


def buyer_ask_command(
    buyer_id: str,
    text: str,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    city: str = "",
    area: str = "",
) -> list[str]:
    args: list[object] = ["buyer", "ask", "--buyer", buyer_id, "--text", text, "--format", "json"]
    if city:
        args.extend(["--city", city])
    if area:
        args.extend(["--area", area])
    return build_mai_command(args, db_path=db_path, project_root=project_root)


def record_intent_command(
    conversation_id: str,
    intent: str,
    text: str,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    return build_mai_command(
        [
            "buyer",
            "intent",
            "--conversation",
            conversation_id,
            "--intent",
            intent,
            "--text",
            text,
            "--format",
            "json",
        ],
        db_path=db_path,
        project_root=project_root,
    )


__all__ = ["DEFAULT_SKILL_ROOT", "build_mai_command", "buyer_ask_command", "record_intent_command", "resolve_project_root"]
