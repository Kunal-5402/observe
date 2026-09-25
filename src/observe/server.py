"""Local UI server: static files from observe/ui plus a small JSON API. Binds to 127.0.0.1 only."""

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from observe import db, normalize, queries

STATIC = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "observe"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            body = resources.files("observe").joinpath("ui", name).read_bytes()
            return self._send(body, f"{ctype}; charset=utf-8")
        conn = db.connect()
        try:
            if url.path == "/api/sessions":
                normalize.ingest(conn)
                agent = parse_qs(url.query).get("agent", [None])[0]
                return self._json(queries.list_sessions(conn, agent if agent in ("claude", "codex") else None))
            if m := re.fullmatch(r"/api/sessions/([\w.-]+)", url.path):
                data = queries.session_detail(conn, m.group(1))
                return self._json(data) if data else self._error(HTTPStatus.NOT_FOUND)
            if m := re.fullmatch(r"/api/events/(\d+)", url.path):
                data = queries.event_detail(conn, int(m.group(1)))
                return self._json(data) if data else self._error(HTTPStatus.NOT_FOUND)
            return self._error(HTTPStatus.NOT_FOUND)
        finally:
            conn.close()

    def _json(self, data) -> None:
        self._send(json.dumps(data).encode(), "application/json")

    def _error(self, status: HTTPStatus) -> None:
        self._send(json.dumps({"error": status.phrase}).encode(), "application/json", status)

    def _send(self, body: bytes, ctype: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(port: int) -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        if port == 0:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
