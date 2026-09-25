import json

from observe import normalize, transcripts


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_claude_usage_dedupes_split_messages(tmp_path):
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7}
    rows = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}},
        {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}},
        {"type": "assistant", "message": {"id": "m2", "model": "claude-opus-5-5", "usage": usage}},
    ]
    path = tmp_path / "t.jsonl"
    write_jsonl(path, rows)
    out = transcripts.parse_claude(str(path))
    assert out["model"] == "claude-opus-5-5"
    assert out["usage"] == {"input_tokens": 20, "output_tokens": 10, "cache_read_tokens": 200, "cache_write_tokens": 14}


def test_codex_rollout_usage_and_web_search(conn, send, isolated):
    sid = "019e7214-0219-7d13-8120-bc515ab426d3"
    day = isolated / "codex" / "sessions" / "2026" / "05" / "29"
    day.mkdir(parents=True)
    write_jsonl(day / f"rollout-2026-05-29T10-22-54-{sid}.jsonl", [
        {"type": "turn_context", "payload": {"model": "gpt-5.5"}},
        {"timestamp": "2026-05-29T04:55:28.951Z", "ordinal": 25, "type": "response_item",
         "payload": {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "sqlite wal"}}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}}}},
    ])
    # transcript_path is null, so the rollout is found by session id.
    send("codex", {"session_id": sid, "hook_event_name": "SessionStart", "cwd": "/r", "transcript_path": None}, 1.0)
    normalize.ingest(conn)
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert (s["model"], s["input_tokens"], s["cache_read_tokens"], s["output_tokens"]) == ("gpt-5.5", 400, 600, 50)
    assert s["tool_call_count"] == 1
    ev = conn.execute("SELECT * FROM events WHERE kind='tool_call'").fetchone()
    assert (ev["category"], ev["target"], ev["source"]) == ("web", "sqlite wal", "transcript")
    normalize.ingest(conn)  # unchanged mtime: nothing is added twice
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='tool_call'").fetchone()[0] == 1
