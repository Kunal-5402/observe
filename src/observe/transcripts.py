"""Token usage and model from the agents' own transcript files.

Hooks carry no token data. Claude Code writes message.usage into its transcript
JSONL; Codex writes token_count events into its rollout JSONL. Files are parsed
again only when their mtime changes.
"""

import json
import os
import sqlite3
from datetime import datetime

from observe import paths


def _lines(path: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            try:
                yield n, json.loads(line)
            except ValueError:
                continue


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_claude(path: str) -> dict:
    usage_by_msg: dict[str, dict] = {}
    model = None
    for n, obj in _lines(path):
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        if msg.get("model") and msg["model"] != "<synthetic>":
            model = msg["model"]
        if msg.get("usage"):
            # One API message is split over several lines with the same id and usage.
            usage_by_msg[msg.get("id") or f"line-{n}"] = msg["usage"]
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for u in usage_by_msg.values():
        total["input_tokens"] += u.get("input_tokens") or 0
        total["output_tokens"] += u.get("output_tokens") or 0
        total["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
        total["cache_write_tokens"] += u.get("cache_creation_input_tokens") or 0
    return {"model": model, "usage": total, "extra_events": []}


def parse_codex(path: str) -> dict:
    last_usage = None
    model = None
    searches = []
    for n, obj in _lines(path):
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        kind = obj.get("type")
        if kind == "turn_context" and payload.get("model"):
            model = payload["model"]
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if info.get("total_token_usage"):
                last_usage = info["total_token_usage"]
        elif kind == "response_item" and payload.get("type") == "web_search_call":
            # Hosted web search does not fire Codex hooks, so take it from the rollout.
            action = payload.get("action") or {}
            ts = _ts(obj.get("timestamp"))
            if ts is not None:
                searches.append(
                    {
                        "tool_use_id": f"codex-ws-{obj.get('ordinal', n)}",
                        "started_at": ts,
                        "target": action.get("query") or action.get("url"),
                        "status": "ok" if payload.get("status") in (None, "completed") else "error",
                        "detail": {"input": action},
                    }
                )
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    if last_usage:
        cached = last_usage.get("cached_input_tokens") or 0
        total["input_tokens"] = max((last_usage.get("input_tokens") or 0) - cached, 0)
        total["output_tokens"] = last_usage.get("output_tokens") or 0
        total["cache_read_tokens"] = cached
        total["cache_write_tokens"] = last_usage.get("cache_write_input_tokens") or 0
    return {"model": model, "usage": total, "extra_events": searches}


def find_codex_rollout(session_id: str) -> str | None:
    root = paths.codex_sessions()
    if not root.is_dir():
        return None
    for p in root.glob(f"*/*/*/rollout-*{session_id}.jsonl"):
        return str(p)
    return None


def refresh(conn: sqlite3.Connection) -> set[str]:
    """Re-read changed transcripts. Returns the ids of sessions that changed."""
    changed = set()
    rows = conn.execute("SELECT id, agent, transcript_path, transcript_mtime FROM sessions").fetchall()
    for s in rows:
        path = s["transcript_path"]
        if not path and s["agent"] == "codex":
            path = find_codex_rollout(s["id"])
        if not path:
            continue
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if s["transcript_mtime"] == mtime:
            continue
        parsed = parse_claude(path) if s["agent"] == "claude" else parse_codex(path)
        u = parsed["usage"]
        with conn:
            conn.execute(
                "UPDATE sessions SET input_tokens=?, output_tokens=?, cache_read_tokens=?, cache_write_tokens=?,"
                " model=COALESCE(?, model), transcript_path=?, transcript_mtime=? WHERE id=?",
                (
                    u["input_tokens"],
                    u["output_tokens"],
                    u["cache_read_tokens"],
                    u["cache_write_tokens"],
                    parsed["model"],
                    path,
                    mtime,
                    s["id"],
                ),
            )
            for e in parsed["extra_events"]:
                conn.execute(
                    "INSERT OR IGNORE INTO events(session_id, agent, kind, category, tool_name, tool_use_id,"
                    " started_at, ended_at, duration_ms, status, target, summary, detail, source)"
                    " VALUES (?, ?, 'tool_call', 'web', 'web_search', ?, ?, ?, 0, ?, ?, ?, ?, 'transcript')",
                    (
                        s["id"],
                        s["agent"],
                        e["tool_use_id"],
                        e["started_at"],
                        e["started_at"],
                        e["status"],
                        e["target"],
                        f"web_search: {e['target']}",
                        json.dumps(e["detail"]),
                    ),
                )
        changed.add(s["id"])
    return changed
