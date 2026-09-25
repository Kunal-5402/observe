# observe

Local telemetry for coding agents. `observe` installs hooks into **Claude Code** and **Codex**,
records every tool call in a local SQLite database, and shows each session as a timeline and a
graph in your browser. Nothing leaves your machine.

What you can see for each session:

- every tool call on a timeline, with its duration and status (idle gaps are compressed)
- the bash commands that ran, and which programs they called
- the files that the agent read and changed (Codex `apply_patch` included)
- the MCP servers and tools that the agent called
- subagents, prompts, context compactions, and interrupts
- token usage and model, read from the agent's own transcript

## Install

```sh
uv tool install .          # or: pipx install .
observe install            # adds hooks to every agent it finds
# or one agent at a time:
observe claude install
observe codex install
```

Start a new agent session after install. Hooks load when a session starts.

## Use

```sh
observe show               # open the UI for all sessions
observe claude show        # only Claude Code sessions
observe codex show [id]    # only Codex sessions, optionally open one session
observe sessions           # list recent sessions in the terminal
observe doctor             # check hooks, database, and recent events
observe uninstall          # remove the hooks (other hooks stay)
```

`observe show` serves the UI on `http://127.0.0.1:7878` (another free port if 7878 is busy).
The page refreshes every 5 seconds while **Live** is on.

## How it works

For more detail, read [design/architecture.md](design/architecture.md) and the call flow in
[design/sequence.md](design/sequence.md).

```
agent hook ──stdin JSON──> observe <agent> hook ──> raw_events (SQLite, WAL)
                                                         │  on show / sessions / ingest
                                    normalize ───────────┴──> sessions, events, files
observe show ──> 127.0.0.1 server ──> static UI + JSON API
```

- **The hook is fast and silent.** It reads the payload, trims long strings, inserts one row,
  and exits 0. It never prints to stdout, because both agents read hook stdout as instructions.
  Failures go to `~/.observe/errors.log`, never to the agent.
- **Normalization is lazy.** Claude Code and Codex send the same payload shape, with different
  tool names. `adapters.py` maps each tool to one of these categories:
  `bash`, `file_read`, `file_write`, `search`, `mcp`, `web`, `agent`, `other`.
  Pre and post events are paired by `tool_use_id`.
- **Tokens come from transcripts.** Hooks carry no token data. `observe` reads the Claude Code
  transcript (`message.usage`) and the Codex rollout file (`token_count`) when they change.
- **Codex web search** does not fire hooks, so `observe` reads those calls from the rollout file.

Config files that `observe` changes (it writes a timestamped backup first):

| Agent       | File                      |
|-------------|---------------------------|
| Claude Code | `~/.claude/settings.json` |
| Codex       | `~/.codex/hooks.json`     |

## Privacy

All data stays in `~/.observe/observe.db`. Each string field is trimmed to 4096 characters
before it is stored, so large file contents and command output are not kept. Set
`OBSERVE_MAX_FIELD` in your shell to change the limit (`0` keeps everything).
Delete `~/.observe/` to remove all recorded data.

## Development

```sh
uv run --group dev pytest
uv run observe show --no-open
```

Environment overrides: `OBSERVE_HOME`, `OBSERVE_CLAUDE_SETTINGS`, `CODEX_HOME`.
