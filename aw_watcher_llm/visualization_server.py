from __future__ import annotations

import mimetypes
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen
from urllib.parse import urlsplit


STATIC_DIR = Path(__file__).resolve().parent.parent / "visualization" / "dist"


def serve_visualization(*, bind: str, port: int, aw_url: str) -> None:
    handler = _build_handler(aw_url=aw_url)
    server = ThreadingHTTPServer((bind, port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_handler(*, aw_url: str):
    class VisualizationHandler(BaseHTTPRequestHandler):
        server_version = "aw-watcher-llm-viewer/0.1"

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self._proxy_get(aw_url)
                return
            if path in {"/", "/index.html"}:
                self._serve_file(STATIC_DIR / "standalone.html")
                return
            candidate = (STATIC_DIR / path.lstrip("/")).resolve()
            try:
                candidate.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self.send_error(404, "Not found")
                return
            if candidate.is_file():
                self._serve_file(candidate)
                return
            self.send_error(404, "Not found")

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _serve_file(self, path: Path) -> None:
            content = path.read_bytes()
            content_type, _ = mimetypes.guess_type(str(path))
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _proxy_get(self, aw_url_base: str) -> None:
            target = f"{aw_url_base.rstrip('/')}{self.path}"
            request = Request(target, headers={"User-Agent": "aw-watcher-llm-viewer"})
            try:
                with urlopen(request, timeout=10) as response:
                    body = response.read()
                    self.send_response(response.status)
                    for key, value in response.headers.items():
                        header = key.lower()
                        if header in {"transfer-encoding", "connection", "content-encoding"}:
                            continue
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            except HTTPError as exc:
                body = exc.read()
                self.send_response(exc.code)
                for key, value in exc.headers.items():
                    header = key.lower()
                    if header in {"transfer-encoding", "connection", "content-encoding"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    return VisualizationHandler
