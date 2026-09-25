"""Add and remove observe hooks in the agents' config files.

Both agents use the same JSON shape:
    {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "..."}]}]}}
Claude Code keeps it in ~/.claude/settings.json, Codex in ~/.codex/hooks.json.
Our entries are found by their command line, so uninstall never touches other hooks.
"""

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

from observe import paths

EVENTS = {
    "claude": [
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "Stop", "SubagentStart", "SubagentStop", "PreCompact", "Notification",
    ],
    "codex": [
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
        "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "Interrupt",
    ],
}
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
# Codex caps these events at 3 seconds.
SHORT_EVENTS = {"SessionEnd", "Interrupt"}
MARKER = re.compile(r"-m observe (claude|codex) hook\b")


def config_path(agent: str) -> Path:
    return paths.claude_settings() if agent == "claude" else paths.codex_hooks()


def hook_command(agent: str) -> str:
    return f"{shlex.quote(sys.executable)} -m observe {agent} hook"


def _load(path: Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _save(path: Path, data: dict) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.observe-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return backup


def strip_hooks(config: dict) -> int:
    """Remove observe hooks from a config dict in place. Returns how many were removed."""
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event, groups in list(hooks.items()):
        kept_groups = []
        for group in groups if isinstance(groups, list) else []:
            entries = group.get("hooks", [])
            kept = [h for h in entries if not MARKER.search(str(h.get("command", "")))]
            removed += len(entries) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del config["hooks"]
    return removed


def add_hooks(config: dict, agent: str) -> None:
    strip_hooks(config)
    hooks = config.setdefault("hooks", {})
    command = hook_command(agent)
    for event in EVENTS[agent]:
        entry = {"type": "command", "command": command, "timeout": 3 if event in SHORT_EVENTS else 10}
        group = {"matcher": "*", "hooks": [entry]} if event in TOOL_EVENTS else {"hooks": [entry]}
        hooks.setdefault(event, []).append(group)


def installed_events(agent: str) -> list[str]:
    try:
        hooks = _load(config_path(agent)).get("hooks") or {}
    except (OSError, ValueError):
        return []
    return [
        event for event, groups in hooks.items()
        if any(MARKER.search(str(h.get("command", ""))) for g in groups for h in g.get("hooks", []))
    ]


def install(agent: str) -> tuple[Path, Path | None]:
    path = config_path(agent)
    config = _load(path)
    add_hooks(config, agent)
    return path, _save(path, config)


def uninstall(agent: str) -> tuple[Path, int]:
    path = config_path(agent)
    if not path.exists():
        return path, 0
    config = _load(path)
    removed = strip_hooks(config)
    if removed:
        _save(path, config)
    return path, removed
