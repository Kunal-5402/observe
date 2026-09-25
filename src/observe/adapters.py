"""Normalization of tool calls from both agents.

Claude Code and Codex send the same hook payload shape (tool_name, tool_input,
tool_response, tool_use_id), but the tool names differ. This module maps a tool
call to a category, a display target, and the files it touches.
"""

import json
import os
import re
import shlex
from dataclasses import dataclass, field

CATEGORIES = ("bash", "file_read", "file_write", "search", "mcp", "web", "agent", "other")

TOOL_CATEGORIES = {
    # Claude Code
    "Bash": "bash",
    "BashOutput": "bash",
    "KillShell": "bash",
    "KillBash": "bash",
    "Read": "file_read",
    "NotebookRead": "file_read",
    "Edit": "file_write",
    "MultiEdit": "file_write",
    "Write": "file_write",
    "NotebookEdit": "file_write",
    "Grep": "search",
    "Glob": "search",
    "LS": "search",
    "WebFetch": "web",
    "WebSearch": "web",
    "Task": "agent",
    "Agent": "agent",
    # Codex
    "shell": "bash",
    "local_shell": "bash",
    "exec_command": "bash",
    "write_stdin": "bash",
    "apply_patch": "file_write",
    "view_image": "file_read",
    "web_search": "web",
    "spawn_agent": "agent",
    "wait_agent": "agent",
    "send_input": "agent",
    "close_agent": "agent",
}

PATCH_LINE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
PATCH_OPS = {"Add File": "write", "Update File": "write", "Move to": "write", "Delete File": "delete"}
EXIT_CODE = re.compile(r"(?:Process exited with code|Exit code:?)\s*(-?\d+)")


@dataclass
class ToolInfo:
    category: str
    target: str | None = None
    files: list[tuple[str, str]] = field(default_factory=list)  # (path, op) with op in read/write/delete


def command_text(tool_input: dict) -> str | None:
    cmd = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(cmd, list):
        if len(cmd) >= 3 and os.path.basename(str(cmd[0])) in ("bash", "sh", "zsh") and cmd[1] in ("-c", "-lc"):
            return str(cmd[2])
        return shlex.join(str(c) for c in cmd)
    return cmd if isinstance(cmd, str) else None


def patch_files(tool_input) -> list[tuple[str, str]]:
    texts = [tool_input] if isinstance(tool_input, str) else [v for v in tool_input.values() if isinstance(v, str)]
    files: list[tuple[str, str]] = []
    for text in texts:
        for kind, path in PATCH_LINE.findall(text):
            item = (path, PATCH_OPS[kind])
            if item not in files:
                files.append(item)
    return files


def _first_string(d: dict) -> str | None:
    for v in d.values():
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:200]
    return None


def classify(tool_name: str | None, tool_input) -> ToolInfo:
    name = tool_name or "unknown"
    ti = tool_input if isinstance(tool_input, dict) else {"input": tool_input} if tool_input is not None else {}

    if name.startswith("mcp__"):
        server, _, tool = name[len("mcp__") :].partition("__")
        return ToolInfo("mcp", f"{server}/{tool}" if tool else server)

    category = TOOL_CATEGORIES.get(name, "other")

    if category == "bash":
        command = command_text(ti)
        reads = bash_reads(command) if command else []
        return ToolInfo(category, command or _first_string(ti), [(p, "read") for p in reads])

    if category == "file_write":
        files = patch_files(ti)
        if files:
            return ToolInfo(category, files[0][0] if len(files) == 1 else f"{len(files)} files", files)

    if category in ("file_read", "file_write"):
        path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
        op = "read" if category == "file_read" else "write"
        return ToolInfo(category, path, [(path, op)] if path else [])

    if category == "search":
        target = ti.get("pattern") or ti.get("query") or ti.get("path")
        if ti.get("path") and target != ti.get("path"):
            target = f"{target}  in {ti['path']}"
        return ToolInfo(category, target)

    if category == "web":
        return ToolInfo(category, ti.get("url") or ti.get("query"))

    if category == "agent":
        parts = [ti.get("subagent_type") or ti.get("agent_type"), ti.get("description")]
        return ToolInfo(category, " · ".join(p for p in parts if p) or _first_string(ti))

    return ToolInfo(category, _first_string(ti))


def response_status(tool_response, hook_event: str) -> str:
    """ok / error / interrupted. Codex PostToolUse has no failure flag, so read exit codes."""
    if hook_event == "PostToolUseFailure":
        return "error"
    if isinstance(tool_response, dict):
        if tool_response.get("is_error") or tool_response.get("isError"):
            return "error"
        if tool_response.get("interrupted"):
            return "interrupted"
        code = tool_response.get("exit_code", tool_response.get("exitCode", tool_response.get("returncode")))
        if isinstance(code, int) and code != 0:
            return "error"
        text = tool_response.get("output") if isinstance(tool_response.get("output"), str) else None
    else:
        text = tool_response if isinstance(tool_response, str) else None
    if text:
        m = EXIT_CODE.search(text[:500])
        if m and int(m.group(1)) != 0:
            return "error"
    return "ok"


def excerpt(value, limit: int = 2000) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n…"


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
SEPARATORS = set(";&|()\n")
PREFIXES = {"sudo", "time", "env", "exec", "nohup", "command", "xargs"}
# Shell keywords and builtins that are not programs worth counting.
NOT_PROGRAMS = {
    "cd",
    "true",
    "false",
    "echo",
    "printf",
    "export",
    "local",
    "set",
    "unset",
    "source",
    ".",
    "read",
    "shift",
    "wait",
    "exit",
    "return",
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "until",
    "do",
    "done",
    "case",
    "esac",
    "in",
    "function",
    "select",
    "{",
    "}",
    "[",
    "[[",
    "]]",
    "!",
}
READERS = {"cat", "head", "tail", "less", "more", "nl", "bat", "wc", "sed"}


def _strip_heredocs(command: str) -> str:
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        out.append(lines[i])
        markers = [m.group(2) for m in HEREDOC.finditer(lines[i])]
        i += 1
        for marker in markers:
            while i < len(lines) and lines[i].strip() != marker:
                i += 1
            i += 1
    return "\n".join(out)


def shell_commands(command: str) -> list[list[str]]:
    """Split a shell line into simple commands. Heredoc bodies, quotes, and redirects are respected."""
    text = HEREDOC.sub(" ", _strip_heredocs(command))
    text = re.sub(r"\d*>&\d+|&>", " ", text)
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.wordchars += "$:@%+,{}[]^!#"
    try:
        tokens = list(lex)
    except ValueError:
        return [text.split()]
    commands, current, skip = [], [], False
    for tok in tokens:
        if tok and all(c in SEPARATORS for c in tok):
            if current:
                commands.append(current)
            current = []
        elif skip:
            skip = False
        elif tok[0] in "<>":
            skip = tok in ("<", ">", ">>")
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands


def _argv(cmd: list[str]) -> list[str]:
    toks = list(cmd)
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in PREFIXES):
        toks.pop(0)
    return toks


def programs(command: str) -> list[str]:
    """Program names in a shell command line: 'cd x && git status | head' -> ['git', 'head']."""
    out = []
    for cmd in shell_commands(command):
        toks = _argv(cmd)
        prog = os.path.basename(toks[0]) if toks else ""
        if prog and prog not in NOT_PROGRAMS and re.fullmatch(r"[\w.+-]+", prog):
            out.append(prog)
    return out


def bash_reads(command: str) -> list[str]:
    """Files that plain reader commands read (cat, head, sed -n, ...). Codex has no Read tool."""
    paths = []
    for cmd in shell_commands(command):
        toks = _argv(cmd)
        if not toks or os.path.basename(toks[0]) not in READERS:
            continue
        prog, args = os.path.basename(toks[0]), toks[1:]
        if prog == "sed":
            if "-n" not in args:
                continue
            args = [a for a in args if not a.startswith("-")][1:]  # the first operand is the sed script
        for arg in args:
            if arg.startswith("-") or arg.isdigit() or any(c in arg for c in "$*?{}"):
                continue
            if arg not in paths:
                paths.append(arg)
    return paths
