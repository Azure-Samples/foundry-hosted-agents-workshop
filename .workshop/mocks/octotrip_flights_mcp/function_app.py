"""Azure Functions host for the OctoTrip Flights mock MCP server.

Deploying this app gives the mock a public HTTPS endpoint, which is what
Foundry needs: hosted MCP tools are called from the Foundry service, not from
your container, so ``http://localhost`` is never reachable.

The protocol is handled by the Functions MCP extension - this module only
declares the ``search`` tool and returns generated data. ``host.json`` sets
``webhookAuthorizationLevel`` to ``Anonymous`` so the endpoint needs no key,
matching the public server it stands in for. The app holds no data and reads
nothing, so there is nothing to protect.

MCP endpoint once deployed:
``https://<app>.azurewebsites.net/runtime/webhooks/mcp``

Note: this module deliberately does *not* use ``from __future__ import
annotations``. ``@app.mcp_tool`` infers each parameter's MCP type from the real
annotation object, and postponed annotations would turn every one of them into
the string ``"string"``.
"""

import json
import logging

import azure.functions as func

from octotrip_mock import call_search, describe, handle_http_request

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# `@app.mcp_tool` reads the tool name from the function name, the description
# from the docstring, and each parameter's type and requiredness from the
# signature. The `@app.mcp_tool_property` decorators below add the wording, and
# must sit *under* `@app.mcp_tool` so they run first. `is_required=False` is
# explicit on every optional parameter: the decorator defaults it to True and
# that override wins over what the signature implies.
@app.mcp_tool()
@app.mcp_tool_property(arg_name="origin", description=describe("origin"))
@app.mcp_tool_property(arg_name="destination", description=describe("destination"))
@app.mcp_tool_property(arg_name="departure_date", description=describe("departure_date"))
@app.mcp_tool_property(
    arg_name="return_date", description=describe("return_date"), is_required=False
)
@app.mcp_tool_property(arg_name="adults", description=describe("adults"), is_required=False)
@app.mcp_tool_property(arg_name="children", description=describe("children"), is_required=False)
@app.mcp_tool_property(arg_name="infants", description=describe("infants"), is_required=False)
@app.mcp_tool_property(arg_name="trip_class", description=describe("trip_class"), is_required=False)
@app.mcp_tool_property(arg_name="currency", description=describe("currency"), is_required=False)
@app.mcp_tool_property(arg_name="locale", description=describe("locale"), is_required=False)
def search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = "",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    trip_class: str = "Y",
    currency: str = "EUR",
    locale: str = "en",
) -> str:
    """Search flight offers between an origin and a destination for the given
    dates. Supports one-way and round-trip searches with flexible passenger
    counts and cabin class. Returns offers grouped by number of stops and
    ranked by price within each group. THIS IS A MOCK: every offer is
    synthetic data generated from the request, not a live fare.
    """
    payload, is_error = call_search(
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "adults": adults,
            "children": children,
            "infants": infants,
            "trip_class": trip_class,
            "currency": currency,
            "locale": locale,
        }
    )
    # The trigger has no channel for MCP's isError flag, so a rejected request
    # comes back as a normal result. The payload is the same either way: the
    # model reads the error code out of the JSON, exactly as it does locally.
    if is_error:
        logging.info("Mock search rejected a request: %s", payload["error"]["code"])

    return json.dumps(payload, ensure_ascii=False, indent=2)


@app.route(route="health", methods=["GET", "HEAD"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Liveness probe, handy right after a deployment."""
    result = handle_http_request(method=req.method, path="/health")
    return func.HttpResponse(body=result.body, status_code=result.status, headers=result.headers)
