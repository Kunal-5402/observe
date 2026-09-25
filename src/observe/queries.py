"""Read models for the CLI and the UI: session lists, event lists, graph, summary."""

import json
import os
import sqlite3
from collections import Counter, defaultdict
from urllib.parse import urlparse

from observe.adapters import CATEGORIES, programs

LEAF_CAP = 30
HUB_LABELS = {
    "bash": "Bash",
    "file_read": "Files read",
    "file_write": "Files changed",
    "search": "Search",
    "mcp": "MCP",
    "web": "Web",
    "agent": "Subagents",
    "other": "Other tools",
}
SESSION_COLS = (
    "id, agent, cwd, model, title, started_at, ended_at, input_tokens, output_tokens, cache_read_tokens,"
    " cache_write_tokens, prompt_count, tool_call_count, error_count"
)


def list_sessions(conn: sqlite3.Connection, agent: str | None = None, limit: int = 500) -> list[dict]:
    sql = f"SELECT {SESSION_COLS} FROM sessions"
    args: list = []
    if agent:
        sql += " WHERE agent=?"
        args.append(agent)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def find_session(conn: sqlite3.Connection, prefix: str) -> str | None:
    rows = conn.execute("SELECT id FROM sessions WHERE id LIKE ? LIMIT 2", (prefix + "%",)).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def event_detail(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["detail"] = json.loads(out["detail"] or "{}")
    out["files"] = [dict(r) for r in conn.execute("SELECT path, op FROM files WHERE event_id=?", (event_id,))]
    return out


def _rel(path: str, cwd: str | None) -> str:
    if cwd and os.path.isabs(path):
        try:
            rel = os.path.relpath(path, cwd)
        except ValueError:
            return path
        if not rel.startswith(".."):
            return rel
    return path


def session_detail(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute(f"SELECT {SESSION_COLS} FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    session = dict(row)
    cwd = session["cwd"]
    events = [
        dict(r)
        for r in conn.execute(
            "SELECT id, kind, category, tool_name, target, summary, started_at, ended_at, duration_ms, status, source"
            " FROM events WHERE session_id=? ORDER BY started_at, id",
            (session_id,),
        )
    ]
    files = [
        {"path": _rel(r["path"], cwd), "op": r["op"], "event_id": r["event_id"]}
        for r in conn.execute("SELECT event_id, path, op FROM files WHERE session_id=?", (session_id,))
    ]
    summary = _summary(events, files)
    return {"session": session, "events": events, "summary": summary, "graph": _graph(session, events, files)}


def _top(counter: Counter, n: int = 12) -> list[dict]:
    return [{"name": k, "count": v} for k, v in counter.most_common(n)]


def _summary(events: list[dict], files: list[dict]) -> dict:
    calls = [e for e in events if e["kind"] == "tool_call"]
    written = Counter(f["path"] for f in files if f["op"] in ("write", "delete"))
    read = Counter(f["path"] for f in files if f["op"] == "read")
    commands = Counter(p for e in calls if e["category"] == "bash" and e["target"] for p in programs(e["target"]))
    mcp = Counter(e["target"].split("/")[0] for e in calls if e["category"] == "mcp" and e["target"])
    return {
        "categories": dict(Counter(e["category"] for e in calls)),
        "files_written": _top(written),
        "files_read": _top(read),
        "commands": _top(commands),
        "mcp_servers": _top(mcp),
        "files_written_total": len(written),
        "files_read_total": len(read),
    }


def _leaf_keys(e: dict, files_by_event: dict) -> list[tuple[str, str, str]]:
    """(node id, label, category) leaves for one tool call."""
    cat, target = e["category"], e["target"] or ""
    if cat in ("file_read", "file_write"):
        return [
            (f"file:{p}", p, "file_write" if op != "read" else "file_read") for p, op in files_by_event.get(e["id"], [])
        ]
    if cat == "bash":
        return [(f"cmd:{p}", p, cat) for p in dict.fromkeys(programs(target))]
    if cat == "mcp":
        server, _, tool = target.partition("/")
        return [(f"mcp:{target}", tool or server, cat)]
    if cat == "web":
        host = urlparse(target).netloc if target.startswith("http") else "search"
        return [(f"web:{host}", host, cat)]
    if cat == "agent":
        return [(f"agent:{target.split(' · ')[0]}", target.split(" · ")[0] or "agent", cat)]
    if cat == "other":
        return [(f"tool:{e['tool_name']}", e["tool_name"] or "unknown", cat)]
    return []


def _graph(session: dict, events: list[dict], files: list[dict]) -> dict:
    files_by_event = defaultdict(list)
    for f in files:
        files_by_event[f["event_id"]].append((f["path"], f["op"]))

    hub_count: Counter = Counter()
    leaf_count: dict[str, Counter] = defaultdict(Counter)
    leaf_meta: dict[str, tuple[str, str]] = {}
    mcp_servers: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        if e["kind"] != "tool_call":
            continue
        cat = e["category"] or "other"
        hub_count[cat] += 1
        if cat == "bash":
            for path, _ in files_by_event.get(e["id"], []):
                hub_count["file_read"] += 1
                leaf_count["file_read"][f"file:{path}"] += 1
                leaf_meta[f"file:{path}"] = (path, "file_read")
        for key, label, leaf_cat in _leaf_keys(e, files_by_event):
            if cat == "mcp":
                server = (e["target"] or "").split("/")[0]
                mcp_servers[server][key] += 1
                leaf_meta[key] = (label, leaf_cat)
                continue
            leaf_count[cat][key] += 1
            leaf_meta[key] = (label, leaf_cat)

    nodes = [{"id": "session", "label": session["title"] or session["id"][:8], "type": "session", "count": 0}]
    links = []
    for cat in CATEGORIES:
        if not hub_count[cat]:
            continue
        hub = f"hub:{cat}"
        nodes.append({"id": hub, "label": HUB_LABELS[cat], "type": "hub", "category": cat, "count": hub_count[cat]})
        links.append({"source": "session", "target": hub, "count": hub_count[cat]})
        if cat == "mcp":
            for server, tools in sorted(mcp_servers.items(), key=lambda kv: -sum(kv[1].values())):
                sid = f"mcp-server:{server}"
                nodes.append(
                    {"id": sid, "label": server, "type": "group", "category": cat, "count": sum(tools.values())}
                )
                links.append({"source": hub, "target": sid, "count": sum(tools.values())})
                _add_leaves(nodes, links, sid, tools, leaf_meta, cat)
            continue
        _add_leaves(nodes, links, hub, leaf_count[cat], leaf_meta, cat)
    return {"nodes": nodes, "links": links}


def _add_leaves(nodes, links, parent, counter: Counter, meta, cat) -> None:
    seen = {n["id"] for n in nodes}
    top = counter.most_common(LEAF_CAP)
    for key, count in top:
        label, leaf_cat = meta[key]
        if key not in seen:
            nodes.append({"id": key, "label": label, "type": "leaf", "category": leaf_cat, "count": count})
            seen.add(key)
        else:
            node = next(n for n in nodes if n["id"] == key)
            node["count"] += count
            if leaf_cat == "file_write":
                node["category"] = "file_write"
        links.append({"source": parent, "target": key, "count": count})
    rest = len(counter) - len(top)
    if rest > 0:
        more = f"more:{parent}"
        nodes.append(
            {
                "id": more,
                "label": f"+{rest} more",
                "type": "more",
                "category": cat,
                "count": sum(counter.values()) - sum(c for _, c in top),
            }
        )
        links.append({"source": parent, "target": more, "count": 1})
