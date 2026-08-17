"""A dependency-free mock of the OctoTrip Flights MCP server.

The mock speaks the same MCP streamable-HTTP protocol and exposes the same
``search`` tool as https://mcp.octotrip.app/flights/mcp, but every offer it
returns is **synthetic data generated from the request itself** — no network
call, no live pricing. Use it when the public server is unavailable, rate
limited, or when you want reproducible answers while working on Step 3.

Only the Python standard library is used, so the same code runs under
``serve_local.py`` (``http.server``) and inside an Azure Functions app.
"""

from .errors import MockToolError
from .flights import search_flights
from .server import SERVER_NAME, SERVER_VERSION, HttpResult, handle_http_request
from .tool import (
    SEARCH_TOOL,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    call_search,
    describe,
    tool_properties_json,
)

__all__ = [
    "HttpResult",
    "MockToolError",
    "SEARCH_TOOL",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "call_search",
    "describe",
    "handle_http_request",
    "search_flights",
    "tool_properties_json",
]
