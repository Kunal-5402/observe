"""observe command line.

observe claude install|uninstall|show|sessions     (hook is called by Claude Code)
observe codex  install|uninstall|show|sessions     (hook is called by Codex)
observe cursor install|uninstall|show|sessions     (hook is called by Cursor)
observe show | sessions | ingest | doctor
"""

import argparse
import os
import shutil
import sys
import time
import webbrowser

from observe import AGENTS, __version__

AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Fast path: the agents call this on every tool use.
    if len(argv) == 2 and argv[0] in AGENTS and argv[1] == "hook":
        from observe import hook

        return hook.run(argv[0])

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="observe", description="Local telemetry for coding agents.")
    parser.add_argument("--version", action="version", version=f"observe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    for agent in AGENTS:
        ap = sub.add_parser(agent, help=f"{AGENT_NAMES[agent]}: install hooks, show sessions")
        asub = ap.add_subparsers(dest="action", required=True, metavar="<action>")
        asub.add_parser("install", help=f"add observe hooks to {AGENT_NAMES[agent]}").set_defaults(
            func=cmd_install, agents=[agent]
        )
        asub.add_parser("uninstall", help="remove observe hooks").set_defaults(func=cmd_uninstall, agents=[agent])
        asub.add_parser("hook", help="internal: record one hook event from stdin").set_defaults(
            func=cmd_hook, agent=agent
        )
        _show_args(asub.add_parser("show", help="open the UI for this agent's sessions")).set_defaults(
            func=cmd_show, agent=agent
        )
        _sessions_args(asub.add_parser("sessions", help="list recent sessions")).set_defaults(
            func=cmd_sessions, agent=agent
        )

    sub.add_parser("install", help="add hooks to every detected agent").set_defaults(func=cmd_install, agents=None)
    sub.add_parser("uninstall", help="remove hooks from every agent").set_defaults(
        func=cmd_uninstall, agents=list(AGENTS)
    )
    _show_args(sub.add_parser("show", help="open the UI for all sessions")).set_defaults(func=cmd_show, agent=None)
    _sessions_args(sub.add_parser("sessions", help="list recent sessions")).set_defaults(func=cmd_sessions, agent=None)
    sub.add_parser("ingest", help="normalize pending hook events now").set_defaults(func=cmd_ingest)
    sub.add_parser("doctor", help="check hooks, database, and recent events").set_defaults(func=cmd_doctor)
    return parser


def _show_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("session", nargs="?", help="session id (or a unique prefix) to open")
    p.add_argument("--port", type=int, default=7878, help="port on 127.0.0.1 (default 7878, falls back to any)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    return p


def _sessions_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("-n", "--limit", type=int, default=20)
    return p


def _detected(agent: str) -> bool:
    from observe import paths

    if agent == "claude":
        return bool(shutil.which("claude")) or paths.claude_settings().parent.is_dir()
    if agent == "cursor":
        return bool(shutil.which("cursor")) or paths.cursor_home().is_dir()
    return bool(shutil.which("codex")) or paths.codex_home().is_dir()


def cmd_install(args) -> int:
    from observe import install

    agents = args.agents or [a for a in AGENTS if _detected(a)]
    if not agents:
        print("No agent found. Run `observe <claude|codex|cursor> install`.")
        return 1
    for agent in agents:
        try:
            path, backup = install.install(agent)
        except ValueError as exc:
            print(f"{AGENT_NAMES[agent]}: cannot read config: {exc}", file=sys.stderr)
            return 1
        print(f"{AGENT_NAMES[agent]}: hooks added to {path}")
        print(f"  command: {install.hook_command(agent)}")
        if backup:
            print(f"  backup: {backup}")
    print("Start a new agent session, then run `observe show`.")
    return 0


def cmd_hook(args) -> int:
    from observe import hook

    return hook.run(args.agent)


def cmd_uninstall(args) -> int:
    from observe import install

    for agent in args.agents:
        path, removed = install.uninstall(agent)
        print(f"{AGENT_NAMES[agent]}: removed {removed} hook(s) from {path}")
    return 0


def cmd_ingest(args) -> int:
    from observe import db, normalize

    conn = db.connect()
    print(f"Processed {normalize.ingest(conn)} raw event(s).")
    return 0


def _fmt_dur(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _fmt_num(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e3 else str(n)


def cmd_sessions(args) -> int:
    from observe import db, normalize, queries

    conn = db.connect()
    normalize.ingest(conn)
    rows = queries.list_sessions(conn, args.agent, args.limit)
    if not rows:
        print("No sessions yet. Install hooks with `observe install`, then use your agent.")
        return 0
    print(
        f"{'STARTED':<17} {'AGENT':<7} {'ID':<9} {'PROJECT':<18} {'TIME':>7} {'TOOLS':>5} {'ERR':>4} "
        f"{'TOKENS':>7}  TITLE"
    )
    for s in rows:
        started = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"])) if s["started_at"] else "-"
        project = os.path.basename(s["cwd"] or "") or "-"
        tokens = s["input_tokens"] + s["output_tokens"] + s["cache_read_tokens"] + s["cache_write_tokens"]
        title = (s["title"] or "").replace("\n", " ")[:50]
        print(
            f"{started:<17} {s['agent']:<7} {s['id'][:8]:<9} {project[:18]:<18} "
            f"{_fmt_dur((s['ended_at'] or 0) - (s['started_at'] or 0)):>7} {s['tool_call_count']:>5} "
            f"{s['error_count']:>4} {_fmt_num(tokens):>7}  {title}"
        )
    return 0


def cmd_show(args) -> int:
    from observe import db, normalize, queries, server

    conn = db.connect()
    normalize.ingest(conn)
    fragment = ""
    if args.session:
        sid = queries.find_session(conn, args.session)
        if not sid:
            print(f"No unique session matches '{args.session}'. Run `observe sessions`.", file=sys.stderr)
            return 1
        fragment = f"#{sid}"
    conn.close()

    httpd = server.make_server(args.port)
    query = f"?agent={args.agent}" if args.agent else ""
    url = f"http://127.0.0.1:{httpd.server_address[1]}/{query}{fragment}"
    print(f"observe UI: {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0


def cmd_doctor(args) -> int:
    from observe import db, install, paths

    ok = True
    print(f"Database: {paths.db_path()}")
    conn = db.connect()
    raw = conn.execute("SELECT COUNT(*), SUM(processed=0), MAX(received_at) FROM raw_events").fetchone()
    print(f"  raw events: {raw[0]}  pending: {raw[1] or 0}")
    for agent in AGENTS:
        name = AGENT_NAMES[agent]
        events = install.installed_events(agent)
        missing = [e for e in install.EVENTS[agent] if e not in events]
        state = "installed" if events and not missing else "partial" if events else "not installed"
        print(f"{name}: hooks {state} ({install.config_path(agent)})")
        if events and missing:
            print(f"  missing events: {', '.join(missing)}. Run `observe {agent} install`.")
            ok = False
        last = conn.execute("SELECT MAX(received_at) FROM raw_events WHERE agent=?", (agent,)).fetchone()[0]
        if last:
            print(f"  last event: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last))}")
        elif events:
            print("  no events received yet. Start a new session of the agent.")
    python = sys.executable
    print(f"Hook interpreter: {python} ({'ok' if os.access(python, os.X_OK) else 'MISSING'})")
    if paths.error_log().exists() and paths.error_log().stat().st_size:
        print(f"Errors were logged: {paths.error_log()}")
        ok = False
    return 0 if ok else 1
