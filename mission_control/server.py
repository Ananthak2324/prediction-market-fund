"""
mission_control/server.py

Standalone SSE server + static page for "Mission Control" — a live agent
visualization for the demo video. Runs entirely separate from the existing
Streamlit dashboard (Streamlit's rerun model isn't built for pushed
real-time events) and from every systemd-managed process — this is purely
an additional read-only observer.

Zero new dependencies: uses only the stdlib (http.server), no fastapi/
uvicorn install required.

Usage:
    python -m mission_control.server            # http://localhost:8765
    python -m mission_control.server --port 9000
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mission_control.watcher import Watcher

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# One Watcher instance shared by the whole process; each connected SSE
# client gets its own fan-out queue so multiple browser tabs can watch
# simultaneously without stealing events from each other.
_watcher = Watcher()
_client_queues: list["queue.Queue[dict]"] = []
_client_queues_lock = threading.Lock()


def _fanout_loop() -> None:
    """Drains the watcher's single event queue and copies each event to
    every currently-connected client's own queue."""
    while True:
        event = _watcher.events.get()  # blocks until an event exists
        with _client_queues_lock:
            targets = list(_client_queues)
        for q in targets:
            q.put(event)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        pass  # keep stdout quiet — the watcher's own prints are the signal

    def do_GET(self) -> None:
        if self.path == "/events":
            self._handle_sse()
        elif self.path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html")
        elif self.path == "/style.css":
            self._serve_static("style.css", "text/css")
        elif self.path == "/app.js":
            self._serve_static("app.js", "application/javascript")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = os.path.join(STATIC_DIR, filename)
        if not os.path.exists(path):
            self.send_response(404)
            self.end_headers()
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        client_q: "queue.Queue[dict]" = queue.Queue()
        with _client_queues_lock:
            _client_queues.append(client_q)

        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = client_q.get(timeout=15)
                    payload = f"data: {json.dumps(event, default=str)}\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # heartbeat comment keeps the connection alive through
                    # proxies/timeouts without counting as a real event
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _client_queues_lock:
                if client_q in _client_queues:
                    _client_queues.remove(client_q)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    _watcher.start_background()
    threading.Thread(target=_fanout_loop, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mission Control running — read-only observer, no writes to any trading file.")
    print(f"Open http://localhost:{args.port} in a browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
