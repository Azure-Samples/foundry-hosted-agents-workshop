"""The ``search`` tool contract, shared by both hosts.

Keeping the schema in one place means the dependency-free local server and the
Azure Functions MCP tool trigger advertise the same tool, with the same
parameters and the same error behaviour.

One deliberate gap: the Functions trigger's ``toolProperties`` carry only name,
type, description and required, so the ``enum``, ``minimum``, ``maximum`` and
defaults below are advertised by the local server but not by the Functions host.
Behaviour is unchanged either way -- ``_normalize`` in ``flights.py`` enforces
all of them server-side and returns an ``invalid_request`` error, so the model
gets told off rather than silently accepting a bad value.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import MockToolError
from .flights import CURRENCY_RATES, search_flights

TOOL_NAME = "search"

TOOL_DESCRIPTION = (
    "Search flight offers between an origin and a destination for the given "
    "dates. Supports one-way and round-trip searches with flexible passenger "
    "counts and cabin class. Returns offers grouped by number of stops and "
    "ranked by price within each group. THIS IS A MOCK: every offer is "
    "synthetic data generated from the request, not a live fare."
)

TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "origin": {
            "type": "string",
            "description": "Departure city, airport name, or IATA code (e.g. 'Frankfurt', 'JFK').",
        },
        "destination": {
            "type": "string",
            "description": "Arrival city, airport name, or IATA code.",
        },
        "departure_date": {
            "type": "string",
            "description": "Departure date (YYYY-MM-DD, DD.MM.YYYY, 'today', or 'tomorrow').",
        },
        "return_date": {
            "type": "string",
            "description": "Return date for a round trip. Omit for one-way.",
        },
        "adults": {
            "type": "integer",
            "minimum": 1,
            "maximum": 9,
            "default": 1,
            "description": "Number of adult passengers (1-9). Defaults to 1.",
        },
        "children": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9,
            "default": 0,
            "description": "Number of children aged 2-11 (0-9). Defaults to 0.",
        },
        "infants": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9,
            "default": 0,
            "description": "Number of infants under 2 (0-9). Defaults to 0.",
        },
        "trip_class": {
            "type": "string",
            "enum": ["Y", "C"],
            "default": "Y",
            "description": "Cabin class: 'Y' for economy (default), 'C' for business.",
        },
        "currency": {
            "type": "string",
            "default": "EUR",
            "description": "ISO 4217 currency code, EUR by default. Supported: "
            + ", ".join(sorted(CURRENCY_RATES))
            + ".",
        },
        "locale": {
            "type": "string",
            "default": "en",
            "description": "Response language code, 'en' by default.",
        },
    },
    "required": ["origin", "destination", "departure_date"],
}

SEARCH_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "inputSchema": TOOL_INPUT_SCHEMA,
}


def describe(property_name: str) -> str:
    """Return the schema description for one parameter.

    The Azure Functions host declares parameters one decorator at a time, so it
    reads its wording from here rather than restating it.
    """
    return TOOL_INPUT_SCHEMA["properties"][property_name]["description"]


def tool_properties_json() -> str:
    """Render the schema as the Azure Functions MCP trigger's ``toolProperties``."""
    required = set(TOOL_INPUT_SCHEMA["required"])
    properties = [
        {
            "propertyName": name,
            "propertyType": spec["type"],
            "description": spec["description"],
            "isRequired": name in required,
        }
        for name, spec in TOOL_INPUT_SCHEMA["properties"].items()
    ]
    return json.dumps(properties)


def call_search(arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Run the tool and return ``(payload, is_error)``.

    Structured errors travel as a normal payload with an ``error`` block, so a
    model gets the retry hint instead of an opaque failure.
    """
    try:
        return search_flights(arguments), False
    except MockToolError as exc:
        return exc.as_payload(), True
