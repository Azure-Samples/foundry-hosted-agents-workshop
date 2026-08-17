"""Tests for the OctoTrip Flights mock MCP server.

The mock stands in for https://mcp.octotrip.app/flights/mcp when that server is
unavailable, so these tests pin the two things a stand-in must get right: the
MCP streamable-HTTP protocol surface, and the promise that results are derived
deterministically from the request.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

# The mock is an Azure Functions app root, not part of the `scripts` package, so
# add its folder to sys.path the same way the Functions host does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mocks" / "octotrip_flights_mcp"))

from octotrip_mock import (  # noqa: E402
    TOOL_DESCRIPTION,
    TOOL_NAME,
    MockToolError,
    handle_http_request,
    search_flights,
    tool_properties_json,
)
from octotrip_mock.airports import AIRPORTS, distance_km, resolve_airport  # noqa: E402
from octotrip_mock.flights import MAX_DETOUR_FACTOR, MAX_DETOUR_SLACK_KM  # noqa: E402
from octotrip_mock.server import DEFAULT_PROTOCOL_VERSION  # noqa: E402

JSON_ACCEPT = "application/json"
MCP_ACCEPT = "application/json, text/event-stream"


def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _post(payload: dict[str, Any] | list[Any], accept: str = JSON_ACCEPT):
    return handle_http_request(
        method="POST",
        path="/mcp",
        accept=accept,
        body=json.dumps(payload).encode("utf-8"),
    )


def _json_body(result) -> Any:
    return json.loads(result.body.decode("utf-8"))


def _call_search(arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Call the ``search`` tool over HTTP and unpack its JSON payload."""
    result = _post(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "search", "arguments": arguments},
        }
    )
    assert result.status == 200
    tool_result = _json_body(result)["result"]
    payload = json.loads(tool_result["content"][0]["text"])
    return payload, tool_result["isError"]


def test_initialize_echoes_a_supported_protocol_version():
    result = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
        }
    )

    body = _json_body(result)["result"]
    assert result.status == 200
    assert body["protocolVersion"] == "2025-03-26"
    assert body["serverInfo"]["name"] == "octotrip-flights-mock"
    assert "tools" in body["capabilities"]


def test_initialize_falls_back_for_an_unknown_protocol_version():
    result = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01", "capabilities": {}},
        }
    )

    assert _json_body(result)["result"]["protocolVersion"] == DEFAULT_PROTOCOL_VERSION


def test_notifications_are_accepted_without_a_body():
    result = _post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert result.status == 202
    assert result.body == b""


def test_tools_list_exposes_the_search_tool():
    result = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    tools = _json_body(result)["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["search"]
    assert tools[0]["inputSchema"]["required"] == ["origin", "destination", "departure_date"]


def test_event_stream_is_used_when_the_client_accepts_it():
    result = _post({"jsonrpc": "2.0", "id": 3, "method": "ping"}, accept=MCP_ACCEPT)

    text = result.body.decode("utf-8")
    assert result.headers["Content-Type"] == "text/event-stream"
    assert text.startswith("event: message\ndata: ")
    assert text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1])["result"] == {}


def test_unknown_method_returns_method_not_found():
    result = _post({"jsonrpc": "2.0", "id": 4, "method": "flights/book"})

    assert _json_body(result)["error"]["code"] == -32601


def test_unparseable_body_returns_a_parse_error():
    result = handle_http_request(method="POST", path="/mcp", accept=JSON_ACCEPT, body=b"{not json")

    assert result.status == 400
    assert _json_body(result)["error"]["code"] == -32700


def test_get_is_rejected_because_the_server_is_stateless():
    result = handle_http_request(method="GET", path="/mcp")

    assert result.status == 405
    assert result.headers["Allow"] == "POST"


def test_health_probe_reports_the_mock():
    result = handle_http_request(method="GET", path="/health")

    assert result.status == 200
    assert _json_body(result) == {
        "status": "ok",
        "server": "octotrip-flights-mock",
        "version": "1.0.0",
        "mock": True,
    }


def test_unknown_tool_returns_an_error_result():
    result = _post(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "book", "arguments": {}}}
    )

    tool_result = _json_body(result)["result"]
    assert tool_result["isError"] is True
    assert json.loads(tool_result["content"][0]["text"])["error"]["code"] == "unknown_tool"


def test_search_results_are_derived_from_the_request():
    arguments = {"origin": "Seattle", "destination": "NRT", "departure_date": _future(30)}

    payload, is_error = _call_search(arguments)

    assert is_error is False
    assert payload["mock"] is True
    assert payload["origin_resolved"]["iata"] == "SEA"
    assert payload["destination_resolved"]["iata"] == "NRT"
    assert payload["total"] == len(payload["results"])
    assert payload["query"]["departure_date"] == arguments["departure_date"]

    for offer in payload["results"]:
        outbound = offer["outbound"]
        assert outbound["legs"][0]["departure"] == "SEA"
        assert outbound["legs"][-1]["arrival"] == "NRT"
        assert len(outbound["legs"]) == offer["stops"] + 1
        assert outbound["departure_date"] == arguments["departure_date"]
        assert offer["return"] is None

    ranking = [(offer["stops"], offer["price"]) for offer in payload["results"]]
    assert ranking == sorted(ranking), "offers are grouped by stops and ranked by price"


def test_identical_requests_replay_identical_results():
    arguments = {"origin": "KEF", "destination": "CPH", "departure_date": _future(45)}

    first, _ = _call_search(arguments)
    second, _ = _call_search(arguments)
    other_day, _ = _call_search({**arguments, "departure_date": _future(46)})

    assert first == second
    assert other_day["results"] != first["results"]


def test_round_trip_and_business_class_cost_more():
    base = {"origin": "FRA", "destination": "MAD", "departure_date": _future(20)}

    one_way, _ = _call_search(base)
    round_trip, _ = _call_search({**base, "return_date": _future(27)})
    business, _ = _call_search({**base, "trip_class": "C"})

    assert round_trip["results"][0]["price"] > one_way["results"][0]["price"]
    assert business["results"][0]["price"] > one_way["results"][0]["price"]
    assert round_trip["results"][0]["return"]["departure_date"] == _future(27)
    assert business["results"][0]["cabin"] == "business"


def test_currency_is_applied_to_prices():
    base = {"origin": "FRA", "destination": "MAD", "departure_date": _future(20)}

    euros, _ = _call_search(base)
    dollars, _ = _call_search({**base, "currency": "usd"})

    assert dollars["query"]["currency"] == "USD"
    assert dollars["results"][0]["currency"] == "USD"
    assert dollars["results"][0]["price"] > euros["results"][0]["price"]

@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    [
        ({"origin": "New York", "destination": "LHR"}, "disambiguation_needed"),
        ({"origin": "Atlantis", "destination": "LHR"}, "airport_not_found"),
        ({"origin": "LHR", "destination": "LHR"}, "no_results"),
        ({"origin": "JFK", "destination": "LHR", "departure_date": "whenever"}, "invalid_date"),
        ({"origin": "JFK", "destination": "LHR", "currency": "XYZ"}, "invalid_request"),
    ],
)
def test_bad_requests_return_structured_errors(arguments: dict[str, Any], expected_code: str):
    payload, is_error = _call_search({"departure_date": _future(15), **arguments})

    assert is_error is True
    assert payload["error"]["code"] == expected_code
    assert payload["error"]["suggestion"]


def test_past_departure_dates_are_rejected():
    with pytest.raises(MockToolError) as excinfo:
        search_flights(
            {"origin": "JFK", "destination": "LHR", "departure_date": "2020-01-01"},
            today=date(2026, 8, 17),
        )

    assert excinfo.value.code == "invalid_date"


def test_unknown_iata_codes_are_rejected_not_invented():
    """A typo should come back as an error, not as a confident fake route."""
    arguments = {"origin": "ZZX", "destination": "LHR", "departure_date": _future(10)}

    payload, is_error = _call_search(arguments)

    assert is_error is True
    assert payload["error"]["code"] == "airport_not_found"
    assert payload["error"]["field"] == "origin"


def test_short_hops_are_non_stop_only():
    """Nobody connects between Lisbon and Porto."""
    payload, is_error = _call_search(
        {"origin": "LIS", "destination": "OPO", "departure_date": _future(10)}
    )

    assert is_error is False
    assert payload["results"], "expected at least one offer"
    assert {offer["stops"] for offer in payload["results"]} == {0}


def test_absurd_dates_are_rejected_rather_than_crashing():
    payload, is_error = _call_search(
        {"origin": "LIS", "destination": "LHR", "departure_date": "9999-12-31"}
    )

    assert is_error is True
    assert payload["error"]["code"] == "invalid_date"


def test_fractional_passenger_counts_are_rejected():
    """int() truncates, so 1.9 must not quietly become one adult."""
    payload, is_error = _call_search(
        {
            "origin": "LIS",
            "destination": "LHR",
            "departure_date": _future(10),
            "adults": 1.9,
        }
    )

    assert is_error is True
    assert payload["error"]["code"] == "invalid_request"


def test_changing_currency_reprices_the_same_flights():
    """Currency is presentation, so it must not shuffle the itineraries."""
    base = {"origin": "FRA", "destination": "MAD", "departure_date": _future(20)}

    euros, _ = _call_search({**base, "currency": "EUR"})
    dollars, _ = _call_search({**base, "currency": "USD"})

    assert [offer["flight_numbers"] for offer in euros["results"]] == [
        offer["flight_numbers"] for offer in dollars["results"]
    ]
    # Same flights, so the rate alone decides the price -- no random spread to
    # swamp it, whatever today's date happens to seed.
    for euro_offer, dollar_offer in zip(euros["results"], dollars["results"]):
        assert dollar_offer["price"] > euro_offer["price"]


def test_time_zones_come_from_the_table_not_from_longitude():
    """Political time zones don't follow meridians; the workshop's cities prove it."""
    assert AIRPORTS["KEF"].utc_offset_hours == 0  # Iceland sits at -22 degrees
    assert AIRPORTS["RAK"].utc_offset_hours == 1  # Morocco at -8 degrees
    assert AIRPORTS["LIS"].utc_offset_hours == 0
    assert AIRPORTS["CDG"].utc_offset_hours == 1


def test_offsets_stay_within_a_few_hours_of_solar_time():
    """Catches a typo'd offset: no real zone is far from its own longitude."""
    for airport in AIRPORTS.values():
        solar = airport.longitude / 15.0
        assert abs(airport.utc_offset_hours - solar) <= 3.5, airport.iata


def test_overnight_tags_only_flights_that_land_on_a_later_date():
    """Eastbound trans-Pacific can land an earlier date -- that isn't overnight."""
    tagged = 0
    for origin, destination in (("HND", "YVR"), ("JFK", "LHR"), ("LIS", "SYD"), ("SFO", "SIN")):
        for days in (10, 40, 90):
            payload, is_error = _call_search(
                {"origin": origin, "destination": destination, "departure_date": _future(days)}
            )
            assert is_error is False
            for offer in payload["results"]:
                overnight = "overnight" in offer["tags"]
                lands_later = (
                    offer["outbound"]["arrival_date"] > offer["outbound"]["departure_date"]
                )
                assert overnight == lands_later
                tagged += overnight

    # Without this the assertion above would pass on a set of offers that never
    # earns the tag at all.
    assert tagged > 0


def test_connections_do_not_zig_zag_across_the_planet():
    """A hub set is only worth offering if the whole path stays near the route."""
    for origin, destination in (("SYD", "AKL"), ("LIS", "RAK"), ("JFK", "LAX"), ("LHR", "CPT")):
        payload, is_error = _call_search(
            {"origin": origin, "destination": destination, "departure_date": _future(25)}
        )
        assert is_error is False
        direct = distance_km(AIRPORTS[origin], AIRPORTS[destination])
        for offer in payload["results"]:
            legs = offer["outbound"]["legs"]
            points = [leg["departure"] for leg in legs] + [legs[-1]["arrival"]]
            flown = sum(
                distance_km(AIRPORTS[a], AIRPORTS[b]) for a, b in zip(points, points[1:])
            )
            assert flown <= direct * MAX_DETOUR_FACTOR + MAX_DETOUR_SLACK_KM, points


def test_same_day_return_never_departs_before_the_outbound_lands():
    """You cannot take off for home before you have landed."""
    for days in (5, 15, 25, 35, 45, 55):
        day = _future(days)
        payload, is_error = _call_search(
            {"origin": "FRA", "destination": "MAD", "departure_date": day, "return_date": day}
        )
        assert is_error is False
        for offer in payload["results"]:
            assert offer["return"]["departure_time"] > offer["outbound"]["arrival_time"]


def test_same_day_return_is_refused_when_the_trip_is_too_long():
    """Sydney and back before midnight isn't a flight, it's a fantasy."""
    day = _future(30)
    payload, is_error = _call_search(
        {"origin": "LIS", "destination": "SYD", "departure_date": day, "return_date": day}
    )

    assert is_error is True
    assert payload["error"]["code"] == "no_results"
    assert payload["error"]["field"] == "return_date"


def test_total_available_does_not_advertise_flights_that_do_not_exist():
    """A bigger number here reads as withheld results the mock cannot produce."""
    payload, _ = _call_search({"origin": "LIS", "destination": "RAK", "departure_date": "tomorrow"})

    assert payload["total_available"] == payload["total"] == len(payload["results"])


def test_an_explicit_null_id_is_answered_rather_than_swallowed():
    """JSON-RPC: "id": null is a request; only a missing id is a notification."""
    body = json.dumps({"jsonrpc": "2.0", "id": None, "method": "tools/list"}).encode("utf-8")
    result = handle_http_request("POST", "/mcp", "application/json", body)

    assert result.status == 200
    assert json.loads(result.body)["id"] is None


def test_an_empty_batch_is_an_invalid_request():
    result = handle_http_request("POST", "/mcp", "application/json", b"[]")

    assert result.status == 400
    assert json.loads(result.body)["error"]["code"] == -32600


def test_head_on_the_health_probe_matches_get():
    """All three hosts should agree that a health probe answers HEAD."""
    head = handle_http_request("HEAD", "/health")
    get = handle_http_request("GET", "/health")

    assert head.status == get.status == 200
    assert handle_http_request("POST", "/health").headers["Allow"] == "GET, HEAD"


def test_partial_words_do_not_resolve_to_an_airport():
    """'port' is in every '... Airport' name and must not pick one."""
    with pytest.raises(MockToolError) as excinfo:
        resolve_airport("port", "origin")
    assert excinfo.value.code in {"airport_not_found", "disambiguation_needed"}


def test_the_real_servers_path_is_accepted_too():
    """Swapping only the host of an existing MCP_SERVER_URL must not 404."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8")
    result = handle_http_request("POST", "/flights/mcp", "application/json", body)

    assert result.status == 200
    assert json.loads(result.body)["result"]["tools"][0]["name"] == "search"


def test_tool_properties_match_the_mcp_trigger_contract() -> None:
    """The Azure Functions trigger takes the same schema in its own shape."""
    properties = json.loads(tool_properties_json())
    by_name = {prop["propertyName"]: prop for prop in properties}

    assert set(by_name) == {
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "adults",
        "children",
        "infants",
        "trip_class",
        "currency",
        "locale",
    }
    required = {name for name, prop in by_name.items() if prop["isRequired"]}
    assert required == {"origin", "destination", "departure_date"}
    assert by_name["adults"]["propertyType"] == "integer"
    assert all(prop["description"] for prop in properties)


def test_the_advertised_tool_name_is_the_one_that_answers() -> None:
    result = _post({"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}})
    listed = _json_body(result)["result"]["tools"]
    assert [tool["name"] for tool in listed] == [TOOL_NAME]

def test_the_functions_host_advertises_the_same_contract() -> None:
    """`function_app.py` must declare exactly what `tool.py` promises.

    Skipped unless `azure-functions` is installed, since the mock's local server
    needs no dependencies at all.
    """
    pytest.importorskip("azure.functions", minversion="1.25.0")

    import function_app  # noqa: PLC0415 - optional dependency

    indexed = {fn.get_function_name(): fn for fn in function_app.app.get_functions()}
    assert "health" in indexed, "the liveness probe should still be registered"

    trigger = next(
        binding.get_dict_repr()
        for binding in indexed["search"].get_bindings()
        if binding.get_dict_repr().get("type") == "mcpToolTrigger"
    )
    assert trigger["toolName"] == TOOL_NAME
    assert " ".join(trigger["description"].split()) == " ".join(TOOL_DESCRIPTION.split())

    from_decorators = {
        prop["propertyName"]: prop for prop in json.loads(trigger["toolProperties"])
    }
    from_schema = {prop["propertyName"]: prop for prop in json.loads(tool_properties_json())}

    assert from_decorators.keys() == from_schema.keys()
    for name, expected in from_schema.items():
        actual = from_decorators[name]
        assert actual["propertyType"] == expected["propertyType"], name
        assert actual["description"] == expected["description"], name
        assert actual["isRequired"] == expected["isRequired"], name

WORKSHOP_ROOT = Path(__file__).resolve().parents[2]


def _rag_cities() -> set[str]:
    """Every destination city the workshop's RAG index ships with."""
    cities: set[str] = set()
    for dataset in WORKSHOP_ROOT.rglob("travel_indexer/data/destinations.json"):
        for entry in json.loads(dataset.read_text(encoding="utf-8")):
            cities.add(entry["city"])
    return cities


def _step_doc_flight_examples() -> set[tuple[str, str]]:
    """The ``City (CODE)`` pairs the step docs use in their flight examples.

    Only lines that mention flights are scanned, and the city name has to sit
    directly in front of the code, so unrelated parenthesised acronyms such as
    ``(MCP)`` or ``(PDF)`` never make it into the results.
    """
    pairs: set[tuple[str, str]] = set()
    for doc in (WORKSHOP_ROOT / "docs" / "steps").glob("*.md"):
        for line in doc.read_text(encoding="utf-8").splitlines():
            if "flight" in line.casefold():
                for city, code in re.findall(r"((?:[A-Z][a-z]+ )+)\(([A-Z]{3})\)", line):
                    pairs.add((city.strip(), code))
    return pairs


def test_every_rag_destination_resolves() -> None:
    """A city the agent can retrieve must be a city the mock can fly you to."""
    cities = _rag_cities()
    assert cities, "expected to find the workshop's destinations index"

    for city in sorted(cities):
        airport = resolve_airport(city, "destination")
        assert airport.country_code != "ZZ", f"{city} falls back to a synthesized airport"
        assert airport.iata in AIRPORTS, city


def test_every_step_doc_flight_example_resolves() -> None:
    """The airports the step docs tell participants to type must be real here."""
    examples = _step_doc_flight_examples()
    assert examples, "expected the step docs to contain flight examples"

    for city, code in sorted(examples):
        assert code in AIRPORTS, f"{city} ({code}) is in a step doc but missing from the mock"
        assert AIRPORTS[code].city == city, f"{code} is {AIRPORTS[code].city} here, not {city}"


def test_multi_airport_cities_pick_their_main_airport() -> None:
    """Asking for 'Tokyo' should fly, not start a negotiation."""
    assert resolve_airport("Tokyo", "origin").iata == "HND"
    assert resolve_airport("London", "origin").iata == "LHR"
    assert resolve_airport("Paris", "origin").iata == "CDG"

    # New York stays ambiguous on purpose: it spans two cities and three airports.
    with pytest.raises(MockToolError) as excinfo:
        resolve_airport("New York", "origin")
    assert excinfo.value.code == "disambiguation_needed"
