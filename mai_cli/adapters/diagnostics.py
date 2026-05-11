"""Shared host-adapter setup and inspection helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def inspect_host(
    host: str,
    command_name: str,
    default_skill_root: Path,
    project_root: str | Path | None = None,
    skill_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser() if project_root is not None else Path(__file__).resolve().parents[2]
    skill = Path(skill_root).expanduser() if skill_root is not None else default_skill_root.expanduser()
    command_path = shutil.which(command_name)
    return {
        "ok": bool(command_path and (root / "scripts" / "mai.py").exists() and skill.exists()),
        "host": host,
        "command": command_name,
        "command_path": command_path or "",
        "command_available": command_path is not None,
        "project_root": str(root),
        "project_root_valid": (root / "scripts" / "mai.py").exists(),
        "skill_root": str(skill),
        "skill_installed": skill.exists(),
        "skill_is_symlink": skill.is_symlink(),
        "db_path": str(Path(db_path).expanduser()) if db_path is not None else "",
    }


def doctor_from_inspection(info: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    if not info["command_available"]:
        issues.append(f"{info['command']} command not found")
    if not info["project_root_valid"]:
        issues.append("mai-cli project root is invalid")
    if not info["skill_installed"]:
        issues.append(f"{info['host']} skill is not installed")
    return {"ok": not issues, "host": info["host"], "issues": issues, "inspection": info}


def install_command(
    target_flag: str,
    project_root: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[str]:
    root = Path(project_root).expanduser() if project_root is not None else Path(__file__).resolve().parents[2]
    command = ["bash", str(root / "scripts" / "install.sh"), target_flag]
    if dry_run:
        command.append("--dry-run")
    if force:
        command.append("--force")
    return command
