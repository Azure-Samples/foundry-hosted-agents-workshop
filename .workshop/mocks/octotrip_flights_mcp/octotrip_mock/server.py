"""The MCP layer: JSON-RPC over streamable HTTP, without any dependency.

The transport matches what the real OctoTrip server does — stateless
streamable HTTP, no session header, no resumability — so an MCP client can be
pointed at this mock by changing one URL. Responses are returned as
``text/event-stream`` when the client accepts it (what MCP clients send) and as
plain ``application/json`` otherwise, which keeps ``curl`` output readable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .tool import SEARCH_TOOL, TOOL_NAME, call_search

SERVER_NAME = "octotrip-flights-mock"
SERVER_VERSION = "1.0.0"

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

# "/flights/mcp" is the real server's path: accepting it means swapping only the
# host of an existing MCP_SERVER_URL points at the mock without a 404.
MCP_PATHS = ("/mcp", "/flights/mcp", "/")
HEALTH_PATH = "/health"

SERVER_INSTRUCTIONS = (
    "Mock stand-in for the OctoTrip Flights MCP server. The 'search' tool returns "
    "synthetic flight offers generated from the request; the data is not real."
)


@dataclass
class HttpResult:
    """A complete HTTP response, ready for whichever host is serving us."""

    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    protocol_version = (
        requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": SERVER_INSTRUCTIONS,
    }


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if name != TOOL_NAME:
        return _tool_result(
            {
                "error": {
                    "code": "unknown_tool",
                    "message": f"Unknown tool {name!r}.",
                    "suggestion": f"This server exposes a single tool: {TOOL_NAME}.",
                },
                "mock": True,
            },
            is_error=True,
        )

    payload, is_error = call_search(params.get("arguments") or {})
    return _tool_result(payload, is_error=is_error)


def handle_message(message: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns ``None`` for notifications."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request: message must be an object.")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "Invalid params: expected an object.")

    if method is None:
        # A response to a server-initiated request; nothing for us to do.
        return None
    if "id" not in message:
        # Notification (e.g. notifications/initialized): acknowledge silently.
        # An explicit "id": null is a request, not a notification, so it is the
        # absent key -- not the None value -- that decides.
        return None

    if method == "initialize":
        return _result(request_id, _handle_initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [SEARCH_TOOL]})
    if method == "tools/call":
        return _result(request_id, _handle_tools_call(params))
    if method in ("resources/list", "prompts/list"):
        # Declared unsupported in initialize, but answer politely if asked.
        key = "resources" if method.startswith("resources") else "prompts"
        return _result(request_id, {key: []})

    return _error(request_id, -32601, f"Method not found: {method}")


def _wants_event_stream(accept: str | None) -> bool:
    return "text/event-stream" in (accept or "").lower()


def _sse_body(responses: list[dict[str, Any]]) -> bytes:
    chunks = [f"event: message\ndata: {json.dumps(response, ensure_ascii=False)}\n\n" for response in responses]
    return "".join(chunks).encode("utf-8")


def _handle_post(body: bytes, accept: str | None) -> HttpResult:
    try:
        message = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return HttpResult(
            status=400,
            body=_json_bytes(_error(None, -32700, f"Parse error: {exc}")),
            headers={"Content-Type": "application/json"},
        )

    if message is None:
        return HttpResult(
            status=400,
            body=_json_bytes(_error(None, -32600, "Invalid Request: empty body.")),
            headers={"Content-Type": "application/json"},
        )

    if isinstance(message, list) and not message:
        return HttpResult(
            status=400,
            body=_json_bytes(_error(None, -32600, "Invalid Request: empty batch.")),
            headers={"Content-Type": "application/json"},
        )

    batch = message if isinstance(message, list) else [message]
    responses = [response for response in (handle_message(item) for item in batch) if response is not None]

    if not responses:
        # Notifications only: the spec expects 202 with no body.
        return HttpResult(status=202)

    if _wants_event_stream(accept):
        return HttpResult(
            status=200,
            body=_sse_body(responses),
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"},
        )

    payload = responses if isinstance(message, list) else responses[0]
    return HttpResult(status=200, body=_json_bytes(payload), headers={"Content-Type": "application/json"})


def handle_http_request(
    method: str,
    path: str,
    accept: str | None = None,
    body: bytes | None = None,
) -> HttpResult:
    """Route one HTTP request to the MCP endpoint or the health probe."""
    normalized_path = "/" + path.split("?", 1)[0].strip("/")
    method = method.upper()

    if normalized_path == HEALTH_PATH:
        if method not in ("GET", "HEAD"):
            return HttpResult(status=405, headers={"Allow": "GET, HEAD"})
        payload = {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION, "mock": True}
        return HttpResult(status=200, body=_json_bytes(payload), headers={"Content-Type": "application/json"})

    if normalized_path not in MCP_PATHS:
        return HttpResult(
            status=404,
            body=_json_bytes({"error": "not_found", "message": f"No handler for {normalized_path}."}),
            headers={"Content-Type": "application/json"},
        )

    if method == "POST":
        return _handle_post(body or b"", accept)

    if method in ("GET", "DELETE"):
        # Stateless server: no server-initiated SSE stream, no session to delete.
        return HttpResult(
            status=405,
            body=_json_bytes(_error(None, -32000, "Method Not Allowed: this server is stateless; use POST.")),
            headers={"Content-Type": "application/json", "Allow": "POST"},
        )

    return HttpResult(status=405, headers={"Allow": "POST"})
