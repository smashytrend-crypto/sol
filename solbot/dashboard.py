"""Prosty panel WWW (bez zależności): http://localhost:8050"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def make_handler(data_dir: Path, bot=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json_file(self, name: str):
            p = data_dir / name
            if p.exists():
                self._send(200, p.read_bytes(), "application/json")
            else:
                self._send(200, b"null", "application/json")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                if bot is not None:
                    with bot.lock:
                        body = json.dumps(bot.snapshot(), ensure_ascii=False).encode()
                    self._send(200, body, "application/json")
                else:
                    self._json_file("paper_snapshot.json")
            elif path == "/api/learn":
                self._json_file("learn_report.json")
            elif path == "/api/backtest":
                self._json_file("backtest_report.json")
            else:
                self._send(404, b"not found", "text/plain")

    return H


def serve(data_dir: str | Path = "data", host: str = "127.0.0.1", port: int = 8050, bot=None, block: bool = True):
    srv = ThreadingHTTPServer((host, port), make_handler(Path(data_dir), bot))
    print(f"Panel: http://{host}:{port}")
    if block:
        srv.serve_forever()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
