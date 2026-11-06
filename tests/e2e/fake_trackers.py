"""Local stand-ins for the GitHub and Jira create-issue APIs, recording what they receive."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve(fail_with: int = 0):
    """(server, base_url, requests) - requests collects (path, headers, body) of every POST."""
    seen: list[tuple[str, dict, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            if fail_with:
                self.send_response(fail_with)
                self.end_headers()
                self.wfile.write(b'{"message": "Bad credentials"}')
                return
            if self.path.endswith("/issues"):  # GitHub
                reply = {"number": 41 + len(seen), "html_url": f"https://github.example/o/r/issues/{41 + len(seen)}"}
            else:  # Jira
                reply = {"key": f"QA-{100 + len(seen)}"}
            payload = json.dumps(reply).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", seen
