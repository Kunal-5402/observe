"""Filesystem locations. Every path can be overridden with an env var (tests use this)."""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("OBSERVE_HOME") or Path.home() / ".observe")


def db_path() -> Path:
    return home() / "observe.db"


def error_log() -> Path:
    return home() / "errors.log"


def claude_settings() -> Path:
    return Path(os.environ.get("OBSERVE_CLAUDE_SETTINGS") or Path.home() / ".claude" / "settings.json")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_hooks() -> Path:
    return codex_home() / "hooks.json"


def codex_sessions() -> Path:
    return codex_home() / "sessions"
