import io
import json

import pytest

from observe import hook, install, normalize, paths
from observe.adapters import classify


def run_hook(monkeypatch, capsys, agent, payload) -> str:
    stdin = type("Stdin", (), {"buffer": io.BytesIO(json.dumps(payload).encode())})()
    monkeypatch.setattr(hook.sys, "stdin", stdin)
    assert hook.run(agent) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize(
    ("event", "reply"),
    [("beforeSubmitPrompt", '{"continue": true}'), ("postToolUse", "{}"), ("stop", "{}")],
)
def test_cursor_hook_always_replies_with_a_noop(monkeypatch, capsys, event, reply):
    assert run_hook(monkeypatch, capsys, "cursor", {"conversation_id": "c", "hook_event_name": event}) == reply


def test_cursor_reply_survives_a_database_failure(monkeypatch, capsys):
    monkeypatch.setattr(hook, "record", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = run_hook(monkeypatch, capsys, "cursor", {"hook_event_name": "beforeSubmitPrompt"})
    assert out == '{"continue": true}'


def test_claude_hook_prints_nothing(monkeypatch, capsys):
    assert run_hook(monkeypatch, capsys, "claude", {"session_id": "s", "hook_event_name": "Stop"}) == ""


def cursor(event, **kw):
    base = {
        "conversation_id": "conv1",
        "generation_id": "g1",
        "hook_event_name": event,
        "model": "claude-sonnet-5",
        "workspace_roots": ["/repo"],
        "user_email": "me@example.com",
    }
    return base | kw


def test_cursor_session_end_to_end(conn, send):
    send("cursor", cursor("sessionStart", session_id="other-id", composer_mode="agent"), 100.0)
    send("cursor", cursor("beforeSubmitPrompt", prompt="add a test"), 101.0)
    shell = {"tool_name": "Shell", "tool_input": {"command": "cat app.py"}, "tool_use_id": "t1"}
    send("cursor", cursor("postToolUse", **shell, tool_output='{"exitCode": 0}', duration=1500), 104.0)
    write = {"tool_name": "Write", "tool_input": {"file_path": "/repo/test_app.py"}, "tool_use_id": "t2"}
    send("cursor", cursor("postToolUse", **write, tool_output="{}", duration=200), 105.0)
    mcp = {"tool_name": "MCP:search_pages", "tool_input": "{}", "tool_use_id": "t3"}
    send("cursor", cursor("postToolUse", **mcp, tool_output="[]", duration=900), 106.0)
    read = {"tool_name": "Read", "tool_input": {"path": "/repo/missing.py"}, "tool_use_id": "t4"}
    failure = {"error_message": "not found", "failure_type": "error", "duration": 10}
    send("cursor", cursor("postToolUseFailure", **read, **failure), 107.0)
    send("cursor", cursor("stop", status="completed"), 108.0)
    normalize.ingest(conn)

    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert (s["id"], s["agent"], s["cwd"], s["model"]) == ("conv1", "cursor", "/repo", "claude-sonnet-5")
    assert (s["prompt_count"], s["tool_call_count"], s["error_count"], s["title"]) == (1, 4, 1, "add a test")

    calls = {r["tool_use_id"]: r for r in conn.execute("SELECT * FROM events WHERE kind='tool_call'")}
    assert (calls["t1"]["category"], calls["t1"]["started_at"], calls["t1"]["duration_ms"]) == ("bash", 102.5, 1500)
    assert calls["t2"]["category"] == "file_write"
    assert (calls["t3"]["category"], calls["t3"]["target"]) == ("mcp", "search_pages")
    assert calls["t4"]["status"] == "error"
    assert json.loads(calls["t4"]["detail"])["error"] == "not found"

    files = {(r["path"], r["op"]) for r in conn.execute("SELECT path, op FROM files")}
    assert files == {("app.py", "read"), ("/repo/test_app.py", "write"), ("/repo/missing.py", "read")}
    assert "me@example.com" not in "".join(r[0] for r in conn.execute("SELECT payload FROM raw_events"))


def test_cursor_tool_names():
    assert classify("ReadFile", {"path": "a.md"}).files == [("a.md", "read")]
    assert classify("StrReplace", {"file_path": "a.py"}).files == [("a.py", "write")]
    assert classify("Delete", {"target_file": "old.py"}).files == [("old.py", "delete")]
    assert classify("Grep", {"pattern": "TODO"}).category == "search"


def test_cursor_install_uses_the_flat_format():
    path = paths.cursor_hooks()
    path.parent.mkdir(parents=True)
    mine = {"command": "./hooks/audit.sh"}
    path.write_text(json.dumps({"version": 1, "hooks": {"stop": [mine]}}))

    install.install("cursor")
    install.install("cursor")
    config = json.loads(path.read_text())
    assert config["version"] == 1
    assert set(config["hooks"]) == set(install.EVENTS["cursor"])
    assert config["hooks"]["stop"][0] == mine
    assert len(config["hooks"]["stop"]) == 2
    assert "-m observe cursor hook" in config["hooks"]["postToolUse"][0]["command"]
    assert not {"preToolUse", "beforeShellExecution", "beforeReadFile"} & set(config["hooks"])
    assert set(install.installed_events("cursor")) == set(install.EVENTS["cursor"])

    _, removed = install.uninstall("cursor")
    assert removed == len(install.EVENTS["cursor"])
    assert json.loads(path.read_text()) == {"version": 1, "hooks": {"stop": [mine]}}
