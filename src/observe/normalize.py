"""Turn raw hook rows into sessions and events.

Runs lazily (on `observe show`, `observe sessions`, `observe ingest`), never in the hook.
"""

import json
import sqlite3
import time
import traceback

from observe import paths, transcripts
from observe.adapters import classify, excerpt, response_status

BATCH = 2000

POINT_EVENTS = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "Stop": "stop",
    "SubagentStart": "subagent",
    "SubagentStop": "subagent",
    "PreCompact": "compact",
    "PostCompact": "compact",
    "Notification": "notification",
    "Interrupt": "interrupt",
    "PermissionRequest": "permission",
}


def ingest(conn: sqlite3.Connection) -> int:
    """Process all pending raw rows. Returns the number of rows processed."""
    total = 0
    touched: set[str] = set()
    while True:
        rows = conn.execute(
            "SELECT * FROM raw_events WHERE processed=0 ORDER BY id LIMIT ?", (BATCH,)
        ).fetchall()
        if not rows:
            break
        with conn:
            for row in rows:
                try:
                    _apply(conn, row, touched)
                except Exception:
                    _log(row["id"])
            conn.executemany("UPDATE raw_events SET processed=1 WHERE id=?", [(r["id"],) for r in rows])
        total += len(rows)
    touched |= transcripts.refresh(conn)
    _finalize(conn, touched)
    return total


def _log(raw_id: int) -> None:
    try:
        with open(paths.error_log(), "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} normalize raw_event {raw_id}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def _apply(conn: sqlite3.Connection, row: sqlite3.Row, touched: set[str]) -> None:
    p = json.loads(row["payload"])
    sid = p.get("session_id") or row["session_id"]
    if not sid:
        return
    agent, ts = row["agent"], row["received_at"]
    event = row["hook_event"] or p.get("hook_event_name") or "unknown"
    _upsert_session(conn, sid, agent, p, ts)
    touched.add(sid)

    if event == "PreToolUse":
        _tool_start(conn, sid, agent, p, ts)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        _tool_end(conn, sid, agent, p, ts, event)
    elif event == "UserPromptSubmit":
        prompt = p.get("prompt") or ""
        _point(conn, sid, agent, "prompt", ts, prompt.strip()[:300], {"prompt": prompt})
    else:
        kind = POINT_EVENTS.get(event, "other")
        _point(conn, sid, agent, kind, ts, _point_summary(event, p), _point_detail(p))


def _point_summary(event: str, p: dict) -> str:
    if event == "SessionStart":
        return f"Session started ({p.get('source') or 'startup'})"
    if event == "SessionEnd":
        return f"Session ended ({p.get('reason') or 'exit'})"
    if event in ("SubagentStart", "SubagentStop"):
        who = p.get("agent_type") or p.get("subagent_type") or "subagent"
        return f"{who} {'started' if event == 'SubagentStart' else 'finished'}"
    if event in ("PreCompact", "PostCompact"):
        return f"Context compaction ({p.get('trigger') or 'auto'})"
    if event == "Notification":
        return (p.get("message") or "Notification")[:300]
    if event == "Stop":
        return "Turn finished"
    if event == "Interrupt":
        return "Interrupted by user"
    return event


def _point_detail(p: dict) -> dict:
    skip = {"session_id", "transcript_path", "cwd", "hook_event_name", "permission_mode"}
    return {k: v for k, v in p.items() if k not in skip}


def _upsert_session(conn, sid, agent, p, ts) -> None:
    conn.execute(
        "INSERT INTO sessions(id, agent, cwd, transcript_path, model, started_at, ended_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        "  cwd=COALESCE(cwd, excluded.cwd),"
        "  transcript_path=COALESCE(excluded.transcript_path, transcript_path),"
        "  model=COALESCE(excluded.model, model),"
        "  started_at=MIN(started_at, excluded.started_at),"
        "  ended_at=MAX(ended_at, excluded.ended_at)",
        (sid, agent, p.get("cwd"), p.get("transcript_path"), p.get("model"), ts, ts),
    )


def _point(conn, sid, agent, kind, ts, summary, detail) -> None:
    conn.execute(
        "INSERT INTO events(session_id, agent, kind, started_at, ended_at, summary, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, agent, kind, ts, ts, summary, json.dumps(detail)),
    )


def _summary(tool_name, target) -> str:
    return f"{tool_name}: {target}"[:300] if target else str(tool_name)


def _set_files(conn, event_id, sid, files) -> None:
    conn.execute("DELETE FROM files WHERE event_id=?", (event_id,))
    conn.executemany(
        "INSERT INTO files(event_id, session_id, path, op) VALUES (?, ?, ?, ?)",
        [(event_id, sid, path, op) for path, op in files],
    )


def _tool_detail(p: dict) -> dict:
    detail = {"input": p.get("tool_input")}
    for key in ("agent_id", "agent_type", "turn_id"):
        if p.get(key):
            detail[key] = p[key]
    return detail


def _tool_start(conn, sid, agent, p, ts) -> None:
    name, tuid = p.get("tool_name"), p.get("tool_use_id")
    info = classify(name, p.get("tool_input"))
    cur = conn.execute(
        "INSERT OR IGNORE INTO events(session_id, agent, kind, category, tool_name, tool_use_id,"
        " started_at, status, target, summary, detail)"
        " VALUES (?, ?, 'tool_call', ?, ?, ?, ?, 'running', ?, ?, ?)",
        (sid, agent, info.category, name, tuid, ts, info.target, _summary(name, info.target),
         json.dumps(_tool_detail(p))),
    )
    if cur.rowcount == 0:  # The Post row arrived first.
        conn.execute(
            "UPDATE events SET started_at=?, duration_ms=CAST((ended_at - ?) * 1000 AS INTEGER)"
            " WHERE session_id=? AND tool_use_id=?",
            (ts, ts, sid, tuid),
        )
        return
    _set_files(conn, cur.lastrowid, sid, info.files)


def _tool_end(conn, sid, agent, p, ts, event) -> None:
    name, tuid = p.get("tool_name"), p.get("tool_use_id")
    response = p.get("tool_response", p.get("error"))
    status = response_status(response, event)
    if tuid:
        row = conn.execute(
            "SELECT id, started_at, detail FROM events WHERE session_id=? AND tool_use_id=?", (sid, tuid)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, started_at, detail FROM events WHERE session_id=? AND tool_name=? AND tool_use_id IS NULL"
            " AND status='running' ORDER BY started_at LIMIT 1",
            (sid, name),
        ).fetchone()

    if row:
        detail = json.loads(row["detail"] or "{}")
        detail["response"] = excerpt(response)
        if p.get("error"):
            detail["error"] = p["error"]
        conn.execute(
            "UPDATE events SET ended_at=?, duration_ms=?, status=?, detail=? WHERE id=?",
            (ts, int((ts - row["started_at"]) * 1000), status, json.dumps(detail), row["id"]),
        )
        return

    info = classify(name, p.get("tool_input"))
    detail = _tool_detail(p) | {"response": excerpt(response)}
    cur = conn.execute(
        "INSERT INTO events(session_id, agent, kind, category, tool_name, tool_use_id, started_at,"
        " ended_at, duration_ms, status, target, summary, detail)"
        " VALUES (?, ?, 'tool_call', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (sid, agent, info.category, name, tuid, ts, ts, status, info.target, _summary(name, info.target),
         json.dumps(detail)),
    )
    _set_files(conn, cur.lastrowid, sid, info.files)


def _finalize(conn, session_ids: set[str]) -> None:
    if not session_ids:
        return
    with conn:
        conn.executemany(
            "UPDATE sessions SET"
            " prompt_count=(SELECT COUNT(*) FROM events WHERE session_id=sessions.id AND kind='prompt'),"
            " tool_call_count=(SELECT COUNT(*) FROM events WHERE session_id=sessions.id AND kind='tool_call'),"
            " error_count=(SELECT COUNT(*) FROM events WHERE session_id=sessions.id AND status='error'),"
            " started_at=MIN(started_at, COALESCE((SELECT MIN(started_at) FROM events"
            "   WHERE session_id=sessions.id), started_at)),"
            " ended_at=MAX(ended_at, COALESCE((SELECT MAX(COALESCE(ended_at, started_at)) FROM events"
            "   WHERE session_id=sessions.id), ended_at)),"
            " title=COALESCE(title, (SELECT summary FROM events WHERE session_id=sessions.id AND kind='prompt'"
            "   ORDER BY started_at LIMIT 1))"
            " WHERE id=?",
            [(sid,) for sid in session_ids],
        )
