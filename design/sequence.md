# End-to-end call flow

This diagram shows 1 full cycle: you install the hooks, the agent works, and then you open the
UI. For the parts and the reasons behind them, see [architecture.md](architecture.md).

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as observe CLI
    participant Config as Agent config file
    participant Agent as Claude Code or Codex
    participant Hook as observe hook
    participant DB as SQLite database
    participant Transcript as Agent transcript
    participant Server as Local server
    participant Browser

    Note over User,Config: 1. Setup, one time
    User->>CLI: observe install
    CLI->>Config: back up the file, then add hook entries
    CLI-->>User: hooks added

    Note over User,Transcript: 2. Capture, during every agent session
    User->>Agent: start a session and send a prompt
    Agent->>Hook: UserPromptSubmit event as JSON on stdin
    Hook->>DB: insert 1 row into raw_events
    Hook-->>Agent: exit 0, no stdout
    loop every tool call
        Agent->>Hook: PreToolUse event
        Hook->>DB: insert raw row
        Agent->>Agent: run the tool
        Agent->>Hook: PostToolUse event with the tool response
        Hook->>DB: insert raw row
    end
    Agent->>Transcript: write messages and token usage
    Agent->>Hook: Stop event
    Hook->>DB: insert raw row

    Note over User,Browser: 3. Normalize and view
    User->>CLI: observe show
    CLI->>DB: read unprocessed raw rows
    CLI->>CLI: classify each tool call and pair Pre with Post by tool_use_id
    CLI->>DB: write sessions, events, and files, then mark raw rows as processed
    CLI->>Transcript: read again only if the file changed
    CLI->>DB: save token counts and model
    CLI->>Server: start on 127.0.0.1 port 7878
    CLI->>Browser: open the UI
    Browser->>Server: GET /api/sessions
    Server->>DB: normalize new raw rows, then list sessions
    Server-->>Browser: session list
    Browser->>Server: GET /api/sessions/{id}
    Server->>DB: read events and files
    Server-->>Browser: events, summary, and graph
    Browser->>Browser: draw the timeline, graph, and event table
    opt click an event
        Browser->>Server: GET /api/events/{id}
        Server-->>Browser: input, response excerpt, and files
    end
    loop every 5 seconds while Live is on
        Browser->>Server: GET /api/sessions
        Server-->>Browser: updated list, and the view redraws if the session changed
    end
```

## What each step means

- **Steps 1 to 3.** `observe install` adds 1 hook entry for each event to the agent config
  file. The command in each entry is `python -m observe <agent> hook`.
- **Steps 4 to 15.** The agent calls the hook for each event. The hook only saves the raw JSON.
  It does not parse it, so the agent does not slow down.
- **Steps 16 to 23.** `observe show` turns the raw rows into sessions, events, and files. Token
  counts come from the agent transcript, because hook events do not contain them.
- **Steps 24 to 34.** The browser gets JSON from the local server. Each request to the session
  list first normalizes any new raw rows, so a running session shows new events.
