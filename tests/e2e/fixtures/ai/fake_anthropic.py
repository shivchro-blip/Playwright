"""A stand-in for the Anthropic Messages API (POST /v1/messages) for tests: no key, no network.

It answers structured-output requests with a canned object picked by the schema's properties, and records
every request so tests can check what would have been sent. Run as a script to serve on a port.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANNED = {
    "category": {"category": "locator_changed", "confidence": "high",
                 "summary": "The field's selector no longer matches anything on the page.",
                 "evidence": ["element(s) not found", "#does-not-exist"], "next_steps": ["Update the locator"],
                 "field": "missing"},
    "suggestions": {"suggestions": [
        {"locator": "#q", "role": None, "name": None, "label": None, "placeholder": None, "text": None,
         "index": None, "rationale": "The search input has id q."},
        {"locator": None, "role": "textbox", "name": None, "label": None, "placeholder": None, "text": None,
         "index": None, "rationale": "Generic textbox."}]},
    "test_case_id": {"test_case_id": "TC-SMOKE-AI-Search", "description": "Search for shoes",
                     "tags": ["smoke"], "steps": [{"kind": "screen", "target": "SmokeHome", "form_data": [
                         {"field": "searchBox", "value": "shoes"}, {"field": "notes", "value": None}]}],
                     "notes": ["Assumed the search box is on SmokeHome."]},
    "headline": {"headline": "All smoke tests passed.", "paragraphs": ["Both checks ran in Chromium and passed."],
                 "action_items": ["Nothing to do."]},
}


class Handler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    status = 200  # tests set an error status or a stop reason for the next answers
    stop_reason = "end_turn"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Handler.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if Handler.status != 200:
            data = json.dumps({"type": "error", "error": {"type": "error", "message": "fake error"}}).encode()
            self.send_response(Handler.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        schema = ((body.get("output_config") or {}).get("format") or {}).get("schema") or {}
        props = schema.get("properties") or {}
        answer = next((v for k, v in CANNED.items() if k in props), {})
        text = [] if Handler.stop_reason == "refusal" else [{"type": "text", "text": json.dumps(answer)}]
        payload = {"id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model", "fake"),
                   "content": text,
                   "stop_reason": Handler.stop_reason,
                   "stop_sequence": None, "usage": {"input_tokens": 1234, "output_tokens": 56}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("request-id", "req_fake")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    srv, url = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(url, flush=True)
    threading.Event().wait()
