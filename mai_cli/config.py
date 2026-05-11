"""Runtime configuration helpers for local mai-cli hosts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "mai-cli"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "mai-cli.sqlite"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "mai-cli"
DEFAULT_AGENT_STALE_TTL_SECONDS = 60


def db_path_from(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MAI_DB") or os.environ.get("MAI_DATA") or DEFAULT_DB_PATH).expanduser()


def state_dir_from(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MAI_CLI_STATE_DIR") or DEFAULT_STATE_DIR).expanduser()


def agent_stale_ttl_seconds_from(value: str | int | None = None) -> int:
    raw = value if value is not None else os.environ.get("MAI_AGENT_STALE_TTL_SECONDS")
    if raw in (None, ""):
        return DEFAULT_AGENT_STALE_TTL_SECONDS
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_AGENT_STALE_TTL_SECONDS
    return seconds if seconds > 0 else DEFAULT_AGENT_STALE_TTL_SECONDS


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: Path = DEFAULT_DB_PATH
    state_dir: Path = DEFAULT_STATE_DIR
    agent_stale_ttl_seconds: int = DEFAULT_AGENT_STALE_TTL_SECONDS

    @classmethod
    def from_env(cls, db_path: str | Path | None = None, state_dir: str | Path | None = None) -> "RuntimeConfig":
        return cls(
            db_path=db_path_from(db_path),
            state_dir=state_dir_from(state_dir),
            agent_stale_ttl_seconds=agent_stale_ttl_seconds_from(),
        )
