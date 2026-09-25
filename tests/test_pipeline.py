import json

from observe import hook, normalize, queries


def claude(event, **kw):
    base = {"session_id": "s1", "cwd": "/repo", "transcript_path": None, "hook_event_name": event}
    return base | kw


def test_claude_session_end_to_end(conn, send):
    send("claude", claude("SessionStart", source="startup"), 100.0)
    send("claude", claude("UserPromptSubmit", prompt="fix the bug"), 101.0)
    send(
        "claude",
        claude("PreToolUse", tool_name="Read", tool_use_id="t1", tool_input={"file_path": "/repo/a.py"}),
        102.0,
    )
    send(
        "claude",
        claude(
            "PostToolUse",
            tool_name="Read",
            tool_use_id="t1",
            tool_input={"file_path": "/repo/a.py"},
            tool_response={"file": {"content": "x"}},
        ),
        102.5,
    )
    send("claude", claude("PreToolUse", tool_name="Bash", tool_use_id="t2", tool_input={"command": "pytest"}), 103.0)
    send(
        "claude",
        claude(
            "PostToolUseFailure", tool_name="Bash", tool_use_id="t2", tool_input={"command": "pytest"}, error="1 failed"
        ),
        105.0,
    )
    send("claude", claude("PreToolUse", tool_name="mcp__notion__search", tool_use_id="t3", tool_input={}), 106.0)
    send("claude", claude("Stop"), 107.0)

    assert normalize.ingest(conn) == 8
    assert normalize.ingest(conn) == 0

    s = conn.execute("SELECT * FROM sessions WHERE id='s1'").fetchone()
    assert (s["prompt_count"], s["tool_call_count"], s["error_count"]) == (1, 3, 1)
    assert s["title"] == "fix the bug"
    assert (s["started_at"], s["ended_at"]) == (100.0, 107.0)

    calls = {r["tool_use_id"]: r for r in conn.execute("SELECT * FROM events WHERE kind='tool_call'")}
    assert calls["t1"]["duration_ms"] == 500 and calls["t1"]["status"] == "ok"
    assert calls["t2"]["status"] == "error" and json.loads(calls["t2"]["detail"])["error"] == "1 failed"
    assert calls["t3"]["status"] == "running"

    detail = queries.session_detail(conn, "s1")
    assert detail["summary"]["files_read"] == [{"name": "a.py", "count": 1}]
    assert detail["summary"]["commands"] == [{"name": "pytest", "count": 1}]
    ids = {n["id"] for n in detail["graph"]["nodes"]}
    assert {"session", "hub:file_read", "hub:bash", "hub:mcp", "file:a.py", "mcp-server:notion"} <= ids


def test_post_before_pre_and_missing_ids(conn, send):
    base = {"session_id": "c1", "cwd": "/r", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    send("codex", base | {"hook_event_name": "PostToolUse", "tool_use_id": "x", "tool_response": "ok"}, 10.0)
    send("codex", base | {"hook_event_name": "PreToolUse", "tool_use_id": "x"}, 9.0)
    send("codex", base | {"hook_event_name": "PreToolUse"}, 11.0)
    send("codex", base | {"hook_event_name": "PostToolUse", "tool_response": "Process exited with code 3"}, 12.0)
    normalize.ingest(conn)
    rows = conn.execute("SELECT tool_use_id, duration_ms, status FROM events ORDER BY started_at").fetchall()
    assert [tuple(r) for r in rows] == [("x", 1000, "ok"), (None, 1000, "error")]


def test_hook_truncates_but_keeps_patch_headers(conn, monkeypatch):
    monkeypatch.setenv("OBSERVE_MAX_FIELD", "100")
    patch = "*** Begin Patch\n*** Update File: a.py\n" + "+x\n" * 200 + "*** Update File: b.py\n+y\n*** End Patch"
    payload = {
        "session_id": "c2",
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_use_id": "p",
        "tool_input": {"command": patch},
    }
    hook.record("codex", json.dumps(payload).encode())
    stored = json.loads(conn.execute("SELECT payload FROM raw_events").fetchone()[0])
    assert len(stored["tool_input"]["command"]) < 300
    normalize.ingest(conn)
    files = [r["path"] for r in conn.execute("SELECT path FROM files ORDER BY path")]
    assert files == ["a.py", "b.py"]


def test_hook_never_raises(monkeypatch, isolated):
    monkeypatch.setattr(hook, "record", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(hook.sys, "stdin", type("S", (), {"buffer": type("B", (), {"read": lambda self: b"{}"})()})())
    assert hook.run("claude") == 0
    assert "boom" in (isolated / "observe" / "errors.log").read_text()
