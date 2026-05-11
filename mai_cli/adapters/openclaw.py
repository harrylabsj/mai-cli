"""OpenClaw host adapter helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from mai_cli.adapters.diagnostics import doctor_from_inspection, inspect_host as inspect_adapter_host
from mai_cli.adapters.diagnostics import install_command as adapter_install_command

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


def merchant_create_command(
    merchant_id: str,
    name: str,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    city: str = "",
    service_area: str = "",
    delivery_eta_minutes: int = 0,
) -> list[str]:
    args: list[object] = ["merchant", "create", "--id", merchant_id, "--name", name, "--format", "json"]
    if city:
        args.extend(["--city", city])
    if service_area:
        args.extend(["--service-area", service_area])
    if delivery_eta_minutes:
        args.extend(["--delivery-eta-minutes", delivery_eta_minutes])
    return build_mai_command(args, db_path=db_path, project_root=project_root)


def product_add_command(
    merchant_id: str,
    sku: str,
    title: str,
    price: float,
    stock: int,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    tags: Iterable[str] | str = (),
) -> list[str]:
    tag_value = ",".join(str(tag) for tag in tags) if not isinstance(tags, str) else tags
    args: list[object] = [
        "product",
        "add",
        "--merchant",
        merchant_id,
        "--sku",
        sku,
        "--title",
        title,
        "--price",
        price,
        "--stock",
        stock,
        "--format",
        "json",
    ]
    if tag_value:
        args.extend(["--tags", tag_value])
    return build_mai_command(args, db_path=db_path, project_root=project_root)


def inspect_host(
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    skill_root: str | Path | None = None,
) -> dict:
    return inspect_adapter_host(
        "OpenClaw",
        "openclaw",
        DEFAULT_SKILL_ROOT,
        project_root=project_root,
        skill_root=skill_root,
        db_path=db_path,
    )


def doctor(
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    skill_root: str | Path | None = None,
) -> dict:
    return doctor_from_inspection(inspect_host(db_path=db_path, project_root=project_root, skill_root=skill_root))


def install_command(project_root: str | Path | None = None, dry_run: bool = False, force: bool = False) -> list[str]:
    return adapter_install_command("--openclaw", project_root=project_root, dry_run=dry_run, force=force)


__all__ = [
    "DEFAULT_SKILL_ROOT",
    "build_mai_command",
    "doctor",
    "inspect_host",
    "install_command",
    "merchant_agent_command",
    "merchant_create_command",
    "product_add_command",
    "resolve_project_root",
]
