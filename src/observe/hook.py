"""Hook entry point. Runs inside the agent's tool loop, so it must be fast and silent.

It reads the payload from stdin, trims large strings, inserts one raw row, and exits 0.
It never writes to stdout: both agents read hook stdout as instructions.
"""

import json
import os
import sys
import time
import traceback

from observe import db, paths

PATCH_MARK = "*** Begin Patch"


def max_field() -> int:
    try:
        return int(os.environ.get("OBSERVE_MAX_FIELD", "4096"))
    except ValueError:
        return 4096


def truncate(value, limit: int):
    """Trim long strings. Patch headers survive, so file paths in big patches are kept."""
    if isinstance(value, str):
        if limit <= 0 or len(value) <= limit:
            return value
        head = value[:limit]
        if PATCH_MARK in value:
            headers = [ln for ln in value.splitlines() if ln.startswith("*** ") and ln not in head]
            head += "\n" + "\n".join(headers)
        return f"{head}\n…[observe: truncated {len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate(v, limit) for v in value]
    return value


def record(agent: str, raw: bytes) -> None:
    limit = max_field()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {"_unparsed": raw.decode(errors="replace")[: limit or None]}
    if not isinstance(payload, dict):
        payload = {"_value": payload}
    payload = truncate(payload, limit)
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO raw_events(agent, hook_event, session_id, received_at, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (agent, payload.get("hook_event_name"), payload.get("session_id"), time.time(), json.dumps(payload)),
            )
    finally:
        conn.close()


def run(agent: str) -> int:
    try:
        record(agent, sys.stdin.buffer.read())
    except Exception:
        try:
            paths.error_log().parent.mkdir(parents=True, exist_ok=True)
            with open(paths.error_log(), "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook {agent}\n{traceback.format_exc()}\n")
        except OSError:
            pass
    return 0
