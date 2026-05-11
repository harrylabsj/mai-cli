"""SQLite connection and serialization helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from mai_cli import VERSION
from mai_cli.db.models import EXTRA_COLUMNS, SCHEMA


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def decode_json(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def open_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        conn.execute(statement)
    for table, columns in EXTRA_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
        for name, definition in columns:
            if name not in existing:
                conn.execute(f"alter table {table} add column {name} {definition}")
    conn.execute(
        """
        update conversations
        set next_actor = case status
            when 'waiting_merchant' then 'merchant_agent'
            when 'waiting_buyer' then 'buyer'
            when 'human_required' then 'merchant_human'
            when 'open' then 'buyer'
            else ''
        end
        where next_actor = ''
        """
    )
    conn.execute(
        "insert or ignore into meta(key, value) values('schema_version', ?)",
        (VERSION,),
    )
    conn.commit()


@contextmanager
def db_session(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
