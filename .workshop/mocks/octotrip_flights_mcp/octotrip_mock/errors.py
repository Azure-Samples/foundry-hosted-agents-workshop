"""Structured tool errors, mirroring the real OctoTrip error contract.

The real server answers a bad request with a machine-readable ``code`` plus a
human ``suggestion`` telling the model how to retry (``airport_not_found``,
``disambiguation_needed``, ``invalid_date``, ``no_results``). The mock raises
``MockToolError`` for the same situations so an agent written against the real
server behaves identically here.
"""

from __future__ import annotations

from typing import Any


class MockToolError(Exception):
    """A tool-level error returned to the caller as an MCP error result."""

    def __init__(
        self,
        code: str,
        message: str,
        suggestion: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}

    def as_payload(self) -> dict[str, Any]:
        """Render the error the way the tool result carries it."""
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
        }
        error.update(self.details)
        return {"error": error, "mock": True}
