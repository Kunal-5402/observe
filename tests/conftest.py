import json

import pytest

from observe import db, hook


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point every observe path at a temp dir, so tests never touch real agent configs."""
    monkeypatch.setenv("OBSERVE_HOME", str(tmp_path / "observe"))
    monkeypatch.setenv("OBSERVE_CLAUDE_SETTINGS", str(tmp_path / "claude" / "settings.json"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("OBSERVE_CURSOR_HOME", str(tmp_path / "cursor"))
    return tmp_path


@pytest.fixture
def conn():
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def send(monkeypatch):
    """Record a hook payload at a fixed clock time, the way the agent would."""

    def _send(agent: str, payload: dict, at: float) -> None:
        monkeypatch.setattr(hook.time, "time", lambda: at)
        hook.record(agent, json.dumps(payload).encode())

    return _send
