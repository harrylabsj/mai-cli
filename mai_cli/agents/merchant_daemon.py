"""Local lifecycle management for resident merchant-agent processes."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mai_cli.agents import merchant_agent
from mai_cli.db.session import db_session, decode_json, now_iso

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "mai-cli"


def state_dir_from(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MAI_CLI_STATE_DIR") or DEFAULT_STATE_DIR).expanduser()


def safe_merchant_id(merchant_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in merchant_id)


def agent_paths(merchant_id: str, state_dir: str | Path | None = None) -> dict[str, Path]:
    root = state_dir_from(state_dir)
    safe_id = safe_merchant_id(merchant_id)
    return {
        "state_dir": root,
        "pid_file": root / "agents" / f"{safe_id}.pid",
        "state_file": root / "agents" / f"{safe_id}.state.json",
        "stop_file": root / "agents" / f"{safe_id}.stop",
        "log_file": root / "logs" / f"{safe_id}.log",
    }


def ensure_agent_dirs(paths: dict[str, Path]) -> None:
    paths["pid_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["log_file"].parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def is_process_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    state = status.stdout.strip().upper()
    if status.returncode != 0 or not state:
        return False
    return "Z" not in state


def read_agent_heartbeat(db_path: str | Path, merchant_id: str) -> dict[str, Any]:
    agent_id = f"mai-cli-merchant-agent:{merchant_id}"
    with db_session(db_path) as conn:
        row = conn.execute("select * from agents where id = ?", (agent_id,)).fetchone()
    if row is None:
        return {
            "id": agent_id,
            "type": "merchant",
            "owner_id": merchant_id,
            "status": "away",
            "capabilities": [],
            "last_seen_at": None,
        }
    return {
        "id": row["id"],
        "type": row["type"],
        "owner_id": row["owner_id"],
        "status": row["status"],
        "capabilities": decode_json(row["capabilities_json"], []),
        "last_seen_at": row["last_seen_at"],
    }


def write_state(
    state_file: Path,
    merchant_id: str,
    running: bool,
    counters: dict[str, int],
    last_error: str | None = None,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = {
        "merchant_id": merchant_id,
        "running": running,
        "pid": pid,
        "updated_at": now_iso(),
        "counters": counters,
        "last_error": last_error,
    }
    if extra:
        state.update(extra)
    write_json_atomic(state_file, state)
    return state


def start_agent(
    db_path: str | Path,
    merchant_id: str,
    interval: float = 3.0,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    paths = agent_paths(merchant_id, state_dir)
    ensure_agent_dirs(paths)
    pid_record = read_json(paths["pid_file"], {})
    existing_pid = int(pid_record.get("pid") or 0)
    stale_replaced = bool(existing_pid and not is_process_running(existing_pid))
    if existing_pid and not stale_replaced:
        raise SystemExit(f"Agent already running for merchant {merchant_id}: pid {existing_pid}")
    if paths["stop_file"].exists():
        paths["stop_file"].unlink()

    with db_session(db_path) as conn:
        merchant_agent.heartbeat(conn, merchant_id, status="online")

    repo_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "mai_cli.cli",
        "--db",
        str(Path(db_path).expanduser()),
        "agent",
        "run",
        "--merchant",
        merchant_id,
        "--interval",
        str(interval),
        "--format",
        "json",
        "--state-file",
        str(paths["state_file"]),
        "--stop-file",
        str(paths["stop_file"]),
    ]
    env = os.environ.copy()
    env["MAI_CLI_STATE_DIR"] = str(paths["state_dir"])
    with paths["log_file"].open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(repo_root),
            env=env,
            start_new_session=True,
        )

    started_at = now_iso()
    pid_payload = {
        "pid": process.pid,
        "merchant_id": merchant_id,
        "db_path": str(Path(db_path).expanduser()),
        "interval": interval,
        "started_at": started_at,
        "command": command,
        "log_file": str(paths["log_file"]),
        "state_file": str(paths["state_file"]),
        "stop_file": str(paths["stop_file"]),
    }
    write_json_atomic(paths["pid_file"], pid_payload)
    write_state(
        paths["state_file"],
        merchant_id,
        running=True,
        counters={"checked": 0, "replied": 0},
        pid=process.pid,
        extra={"started_at": started_at},
    )
    return {
        "ok": True,
        "merchant_id": merchant_id,
        "pid": process.pid,
        "running": True,
        "stale_replaced": stale_replaced,
        "pid_file": str(paths["pid_file"]),
        "state_file": str(paths["state_file"]),
        "stop_file": str(paths["stop_file"]),
        "log_file": str(paths["log_file"]),
    }


def stop_agent(
    db_path: str | Path,
    merchant_id: str,
    state_dir: str | Path | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    paths = agent_paths(merchant_id, state_dir)
    pid_record = read_json(paths["pid_file"], {})
    pid = int(pid_record.get("pid") or 0)
    was_running = is_process_running(pid)
    paths["stop_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["stop_file"].write_text(now_iso(), encoding="utf-8")
    if was_running:
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = read_json(paths["state_file"], {})
            if state.get("running") is False or not is_process_running(pid):
                break
            time.sleep(0.1)
    state_after_stop = read_json(paths["state_file"], {})
    running = is_process_running(pid) and state_after_stop.get("running") is not False
    if not running and paths["pid_file"].exists():
        paths["pid_file"].unlink()

    with db_session(db_path) as conn:
        merchant_agent.heartbeat(conn, merchant_id, status="away")

    previous = read_json(paths["state_file"], {})
    counters = previous.get("counters") or {"checked": 0, "replied": 0}
    write_state(
        paths["state_file"],
        merchant_id,
        running=False,
        counters=counters,
        last_error=previous.get("last_error"),
        pid=pid or None,
        extra={"stopped_at": now_iso(), "stop_timeout": running},
    )
    return {
        "ok": not running,
        "merchant_id": merchant_id,
        "pid": pid or None,
        "was_running": was_running,
        "running": running,
        "pid_file": str(paths["pid_file"]),
        "state_file": str(paths["state_file"]),
        "stop_file": str(paths["stop_file"]),
        "log_file": str(paths["log_file"]),
    }


def status_agent(db_path: str | Path, merchant_id: str, state_dir: str | Path | None = None) -> dict[str, Any]:
    paths = agent_paths(merchant_id, state_dir)
    pid_record = read_json(paths["pid_file"], {})
    pid = int(pid_record.get("pid") or 0)
    state = read_json(paths["state_file"], {})
    running = is_process_running(pid) and state.get("running") is not False
    counters = state.get("counters") or {"checked": 0, "replied": 0}
    return {
        "ok": True,
        "merchant_id": merchant_id,
        "pid": pid or None,
        "running": running,
        "stale_pid": bool(pid and not running),
        "pid_file": str(paths["pid_file"]),
        "state_file": str(paths["state_file"]),
        "stop_file": str(paths["stop_file"]),
        "log_file": str(paths["log_file"]),
        "heartbeat": read_agent_heartbeat(db_path, merchant_id),
        "counters": {
            "checked": int(counters.get("checked") or 0),
            "replied": int(counters.get("replied") or 0),
        },
        "last_error": state.get("last_error"),
        "started_at": pid_record.get("started_at") or state.get("started_at"),
        "updated_at": state.get("updated_at"),
    }


def logs_agent(merchant_id: str, tail: int = 20, state_dir: str | Path | None = None) -> dict[str, Any]:
    paths = agent_paths(merchant_id, state_dir)
    entries: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    try:
        raw_lines = paths["log_file"].read_text(encoding="utf-8").splitlines()[-tail:]
    except FileNotFoundError:
        raw_lines = []
    for line in raw_lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            parsed = {"event": "raw", "text": line}
        entries.append(parsed)
    return {"ok": True, "merchant_id": merchant_id, "log_file": str(paths["log_file"]), "entries": entries}


def run_forever(
    db_path: str | Path,
    merchant_id: str,
    interval: float = 3.0,
    state_file: str | Path | None = None,
    stop_file: str | Path | None = None,
) -> None:
    stop_requested = False
    counters = {"checked": 0, "replied": 0}
    last_error: str | None = None
    state_path = Path(state_file).expanduser() if state_file else None
    stop_path = Path(stop_file).expanduser() if stop_file else None

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop_requested and not (stop_path and stop_path.exists()):
            try:
                with db_session(db_path) as conn:
                    result = merchant_agent.process_once(conn, merchant_id)
                checked = int(result.get("checked") or 0)
                replied_count = len(result.get("replied") or [])
                counters["checked"] += checked
                counters["replied"] += replied_count
                last_error = None
                event = {
                    "event": "process_once",
                    "at": now_iso(),
                    "merchant_id": merchant_id,
                    "checked": checked,
                    "replied_count": replied_count,
                    "counters": counters,
                    "result": result,
                }
            except Exception as exc:  # pragma: no cover - defensive runtime path
                last_error = f"{type(exc).__name__}: {exc}"
                event = {
                    "event": "error",
                    "at": now_iso(),
                    "merchant_id": merchant_id,
                    "counters": counters,
                    "error": last_error,
                }
            print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
            if state_path:
                write_state(state_path, merchant_id, running=True, counters=counters, last_error=last_error, pid=os.getpid())

            deadline = time.time() + max(interval, 0.05)
            while not stop_requested and not (stop_path and stop_path.exists()) and time.time() < deadline:
                time.sleep(min(0.1, max(deadline - time.time(), 0.01)))
    finally:
        try:
            with db_session(db_path) as conn:
                merchant_agent.heartbeat(conn, merchant_id, status="away")
        finally:
            if state_path:
                write_state(
                    state_path,
                    merchant_id,
                    running=False,
                    counters=counters,
                    last_error=last_error,
                    pid=os.getpid(),
                    extra={"stopped_at": now_iso()},
                )
            if stop_path and stop_path.exists():
                stop_path.unlink()
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
