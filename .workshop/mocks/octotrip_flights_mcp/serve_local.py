#!/usr/bin/env python
"""Run the OctoTrip Flights mock MCP server on this machine.

    python .workshop/mocks/octotrip_flights_mcp/serve_local.py

The MCP endpoint is then http://127.0.0.1:8931/mcp and a health probe sits at
http://127.0.0.1:8931/health. Foundry calls MCP servers from the service side,
so a localhost URL only works for MCP clients running on this machine — expose
it with a dev tunnel, or deploy the Azure Functions variant, when Foundry has
to reach it. See README.md.
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from octotrip_mock import SERVER_NAME, SERVER_VERSION, handle_http_request  # noqa: E402

MAX_BODY_BYTES = 1 * 1024 * 1024


class MockMcpHandler(BaseHTTPRequestHandler):
    """Adapts ``http.server`` onto the shared request handler."""

    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"

    def _dispatch(self, method: str) -> None:
        body = b""
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            content_length = 0
        if content_length > MAX_BODY_BYTES:
            self.send_response(413)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if content_length:
            body = self.rfile.read(content_length)

        result = handle_http_request(
            method=method,
            path=self.path,
            accept=self.headers.get("Accept"),
            body=body,
        )

        self.send_response(result.status)
        for name, value in result.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(result.body)))
        self.end_headers()
        if result.body:
            self.wfile.write(result.body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - http.server naming
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base class signature
        print(f"[{SERVER_NAME}] {self.address_string()} {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8931, help="Port to listen on (default: 8931).")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), MockMcpHandler)
    print(f"{SERVER_NAME} {SERVER_VERSION} listening on http://{args.host}:{args.port}/mcp")
    print("Synthetic data only — no live flights. Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
