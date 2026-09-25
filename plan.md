# Plan: local agent telemetry package (Claude Code + Codex)

Package name: `observe`. Commands: `observe claude <action>`, `observe codex <action>`.

## Status (v0.1 built)
All build-order steps are done. Differences from the first draft:
- `resources` table became `files(event_id, session_id, path, op)`; commands, MCP servers,
  and domains are derived at query time (`queries.py`).
- UI is vanilla JS + SVG (no vendored libs): own timeline and a small force layout.
- Timeline compresses idle gaps (> 60s with no events), so long user waits do not flatten it.
- Codex reads files through shell commands, so `cat/head/tail/sed -n/nl/...` arguments are
  recorded as file reads. Codex hosted web search has no hook; it is read from the rollout file.
- Hook latency measured: ~28 ms median per event, no stdout.

Next ideas: backfill past sessions from transcripts (`observe claude import`), cost estimate per
model, cross-session views (top files / commands per project), export to JSON.

## Context
Users of Claude Code and Codex cannot easily see what their agent did in a session:
which tools it called, which bash commands it ran, which files it read or changed,
which MCP servers it used, and how many tokens it spent. The package installs hooks
into both agents, stores normalized events in a local SQLite database, and shows each
session as a timeline and a node graph in a local static HTML page (`observe show`).
Everything stays on the device.

## Architecture
```
agent hook (stdin JSON) ──> observe hook <agent> <event> ──> raw_events (SQLite, WAL)
                                                               │
                              normalize (lazy, on `show` / `ingest`) ──> events, sessions
                                                               │
   observe show ──> local HTTP server (127.0.0.1) ──> static HTML + JSON API
```
Rule: the hook path is fast. It reads stdin, inserts 1 raw row, exits 0. It never blocks
the agent and never prints to stdout (stdout can change agent behavior in Claude Code).
All parsing happens in the normalization step.

## Project layout
```
pyproject.toml              # hatchling, console script `observe`, zero runtime deps
src/observe/
  cli.py                    # argparse: install, uninstall, hook, show, ingest, sessions, doctor
  paths.py                  # data dir: ~/.observe/ (db, logs)
  db.py                     # connect(), schema migrations, WAL mode
  hook.py                   # entry for hooks: read stdin -> insert raw_events
  install/
    claude.py               # merge hooks into ~/.claude/settings.json (backup first)
    codex.py                # merge hooks into ~/.codex hooks config (backup first)
  adapters/
    base.py                 # Adapter protocol: normalize(raw) -> list[Event]
    claude.py               # Claude Code hook payload -> Event
    codex.py                # Codex hook payload -> Event
  transcripts/
    claude.py               # parse transcript_path JSONL: tokens, model, per-turn usage
    codex.py                # parse ~/.codex/sessions rollout JSONL: tokens, model
  normalize.py              # pair Pre/Post by tool_use_id, classify, write events/sessions
  classify.py               # tool -> category (bash, file_read, file_write, mcp, web, agent, other)
  server.py                 # http.server: serves ui/ + /api/sessions, /api/session/<id>
  ui/index.html, app.js, style.css   # vendored JS libs, no CDN (works offline)
tests/                      # pytest, fixture payloads per agent/event
```

## Data model (SQLite)
- `raw_events(id, agent, hook_event, received_at, payload_json, processed)`
- `sessions(id, agent, cwd, model, started_at, ended_at, input_tokens, output_tokens,
  cache_read_tokens, prompt_count, tool_call_count)`
- `events(id, session_id, agent, kind, category, tool_name, tool_use_id, started_at,
  ended_at, duration_ms, status, summary, target, detail_json)`
  - `kind`: session_start, prompt, tool_call, subagent, stop, compact, notification
  - `category`: bash, file_read, file_write, search, mcp, web, agent, other
  - `target`: file path, bash command, MCP `server/tool`, or URL
- `resources(session_id, type, value, count)` — files, MCP servers, commands; feeds the graph.

## Normalization rules
- Pair PreToolUse and PostToolUse by `tool_use_id` (fallback: session + tool + order).
  Duration = post − pre. A Pre without a Post = `status: incomplete`.
- Claude Code categories: `Bash`→bash; `Read`→file_read; `Edit`/`Write`/`MultiEdit`/
  `NotebookEdit`→file_write; `Grep`/`Glob`→search; `WebFetch`/`WebSearch`→web;
  `Task`/`Agent`→agent; `mcp__<server>__<tool>`→mcp (server parsed from the name).
- Codex categories: shell/exec→bash; apply_patch→file_write (parse patch headers for paths);
  MCP tool names→mcp. Verify exact Codex tool names and hook payload fields in step 1.
- Tokens: hooks do not carry tokens. On `Stop`/`SessionEnd`, read the transcript file
  (Claude: `transcript_path`; Codex: rollout file by session id) and update `sessions`.
- Redaction: truncate large `tool_response` bodies (default 4 KB); config option to drop
  outputs completely. Do not store env vars.

## CLI
- `observe claude|codex install|uninstall` — back up config, add or remove hooks (idempotent;
  our hooks are found by their command line, so other hooks stay).
- `observe install|uninstall` — all detected agents.
- `observe claude|codex hook` — internal, called by the agent (event name comes from the payload).
- `observe [claude|codex] show [session_id]` — normalize pending rows, serve the UI on
  127.0.0.1:7878, open the browser.
- `observe [claude|codex] sessions` — table in the terminal. `observe doctor`, `observe ingest`.

Hook command uses the absolute path of the installed Python/entry point, so it works
from any cwd and any virtualenv.

## UI
- Session list: agent, project (cwd), start time, duration, tool calls, tokens.
- Session page, 2 tabs:
  1. **Timeline**: one row per event, bar width = duration, color = category, click for
     detail (input, output excerpt, status). Prompts shown as markers.
  2. **Graph**: session node in the center; nodes for tools, files, MCP servers, commands;
     edge weight = count. Use a vendored force-graph lib (e.g. cytoscape.js).
- Summary panel: top files changed, bash commands, MCP servers, token totals.

## Build order
1. Capture spike: install a hook that dumps raw stdin to a file for both agents. Record
   real payloads for every event as test fixtures. Confirm Codex hook config format.
2. `db.py`, `hook.py`, `install/claude.py`, `install/codex.py`, `cli.py` (install/hook).
3. Adapters + `normalize.py` + `classify.py` with tests over fixtures.
4. Transcript parsers for tokens.
5. `server.py` + session list + timeline.
6. Graph tab + summary panel.
7. `doctor`, `uninstall`, redaction config, README.

## Verification
- `pytest`: adapters over recorded fixtures; Pre/Post pairing; idempotent install/uninstall
  on a temp settings file.
- Hook latency: `time observe hook claude PreToolUse < fixture.json` stays under ~50 ms.
- End to end: `pip install -e .`, `observe install`, run a short Claude Code session and a
  Codex session (read a file, edit a file, run bash, call an MCP tool), then
  `observe show` and check that both tabs show every action with the right category.
- `observe uninstall` restores the original settings files.
