# Architecture

`observe` shows you what your coding agent did during a session: which tools it called, which
commands it ran, which files it read and changed, which MCP servers it used, and how many tokens
it spent. It works with Claude Code, Codex, and Cursor. All data stays on your machine.

For the step-by-step call flow, see [sequence.md](sequence.md).

## The big picture

`observe` has 3 parts. Each part runs at a different time.

```
 1. CAPTURE                    2. NORMALIZE                      3. VIEW
 (inside the agent,            (when you run observe show,       (in your browser)
  on every event)               sessions, or ingest)

 agent ──> observe hook ──>  raw_events ──> normalize ──>  sessions   ──> local server ──> UI
                             (SQLite)          │           events           (JSON API)
                                               │           files
                             transcripts ──────┘
                             (tokens, model)
```

1. **Capture.** The agent runs `observe <agent> hook` on each hook event and sends the event as
   JSON on stdin. The hook saves the event as it is, in 1 row of the `raw_events` table, and exits.
2. **Normalize.** Later, `observe` reads the new raw rows and turns them into clean records:
   1 row for each session, 1 row for each tool call or prompt, and 1 row for each file touched.
   It also reads the agent's transcript file to get token counts and the model name.
3. **View.** `observe show` starts a small web server on `127.0.0.1` and opens the UI. The UI
   asks the server for JSON and draws the timeline, the graph, and the event table.

## Components

| File | Job |
|------|-----|
| `cli.py` | The `observe` command. Parses arguments and calls the other modules. |
| `hook.py` | Runs inside the agent. Reads stdin, trims long strings, inserts 1 raw row. |
| `install.py` | Adds and removes our hooks in the agent config files. Makes a backup first. |
| `db.py` | Opens SQLite (WAL mode) and creates the tables. |
| `normalize.py` | Turns raw rows into sessions, events, and files. Pairs pre and post tool events. |
| `adapters.py` | The rules for each agent: maps a tool name to a category, a target, and files. |
| `transcripts.py` | Reads Claude Code transcripts and Codex rollout files for tokens and model. |
| `queries.py` | Builds what the CLI and UI show: session lists, summaries, and the graph. |
| `server.py` | Serves the UI files and the JSON API. |
| `ui/` | The web page: plain HTML, CSS, and JavaScript. No build step. No external libraries. |
| `paths.py` | Where files live. Every path can be changed with an environment variable. |

## Data model

All data is in `~/.observe/observe.db`.

| Table | One row is | Written by |
|-------|------------|------------|
| `raw_events` | 1 hook event, exactly as the agent sent it (after trimming) | the hook |
| `sessions` | 1 agent session: project folder, model, times, token and call counts | normalize |
| `events` | 1 tool call, prompt, turn end, compaction, subagent, or interrupt | normalize |
| `files` | 1 file that a tool call read, wrote, or deleted | normalize |

Each tool call has a **category**: `bash`, `file_read`, `file_write`, `search`, `mcp`, `web`,
`agent`, or `other`. The category sets the lane on the timeline, the hub in the graph, and the
color everywhere.

## Design decisions

**The hook must be fast and silent.** The agent waits for the hook before it continues, so a slow
hook makes the agent slow. The hook does no parsing: it inserts 1 row and exits in about 30 ms.
For Claude Code and Codex it never writes to stdout, because they read hook stdout as
instructions. It never fails the agent: errors go to `~/.observe/errors.log`, and the exit code
is always 0.

**Cursor gets a reply that changes nothing.** Cursor reads hook stdout as JSON on every event,
and for some events empty output can block the action. So for Cursor the hook always prints `{}`,
or `{"continue": true}` for a prompt. It prints this reply even when the database write fails.

**Cursor hooks only observe.** Some Cursor hooks decide if an action may run (`preToolUse`,
`beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`, `subagentStart`). If `observe`
answered "allow" there, it could skip a question that Cursor would normally ask you. So `observe`
subscribes only to events that report what happened. `postToolUse` carries the duration of the
call, so `observe` can calculate when the call started.

**Normalization is lazy.** Parsing happens only when you look at the data. This keeps the hook
small, and lets us fix a parsing bug and re-process old events without a new capture.

**One schema for all agents.** The agents send a similar hook payload shape (`tool_name`,
`tool_input`, `tool_use_id`). The differences are small: Cursor names the session
`conversation_id`, uses camelCase event names (`postToolUse`), and calls the result
`tool_output`. `adapters.py` holds these differences, so the rest of the code and the UI do not
need to know which agent made an event.

**Tokens come from transcripts.** Hook payloads have no token counts. Each agent writes its own
transcript file: Claude Code writes `message.usage`, and Codex writes `token_count` events.
`observe` reads a transcript again only when its modification time changes.

**Local only, no dependencies.** The package uses only the Python standard library. The server
listens on `127.0.0.1` only. Nothing is sent over the network.

**Long strings are trimmed.** Each string in a payload is cut to 4096 characters before it is
stored. File contents and long command output are not kept. For a Codex patch, the lines with
file names are kept, so we still know every file the patch changed.

**The timeline compresses idle time.** A tool call can wait hours for your answer. If the
timeline showed real time, all other calls would be too thin to see. Gaps of more than 60 seconds
with no events become thin breaks.

## Differences between the agents

| Topic | Claude Code | Codex | Cursor |
|-------|-------------|-------|--------|
| Config file | `~/.claude/settings.json` | `~/.codex/hooks.json` | `~/.cursor/hooks.json` |
| Session id | `session_id` | `session_id` | `conversation_id` |
| Tool call events | Pre and Post | Pre and Post | Post only, with `duration` |
| Hook stdout | must be empty | must be empty | must be JSON |
| File reads | `Read` tool | Shell commands. We detect `cat`, `head`, `sed -n`, and others. | `Read` tool |
| File writes | `Edit`, `Write`, `MultiEdit` | `apply_patch`. We read file names from the patch. | `Write`, `Delete` |
| MCP tool name | `mcp__server__tool` | `mcp__server__tool` | `MCP:tool` |
| Failed tool calls | `PostToolUseFailure` event | No failure flag. We read the exit code. | `postToolUseFailure` event |
| Web search | `WebSearch` tool fires hooks | No hook. We read it from the rollout file. | Tool event |
| Tokens | Transcript `message.usage` | Rollout `token_count` events | Not available |

## How to add a new agent

1. Add the agent name to `AGENTS` in `__init__.py`.
2. Add its config file path to `paths.py`, and its hook events to `EVENTS` in `install.py`.
   Subscribe only to events that report, never to events that decide.
   If its event names differ, map them in `EVENT_ALIASES` in `normalize.py`.
3. Add its tool names to `TOOL_CATEGORIES` in `adapters.py`.
4. If it writes a transcript, add a parser to `transcripts.py`.
5. Record real payloads from the agent and add them as tests.
