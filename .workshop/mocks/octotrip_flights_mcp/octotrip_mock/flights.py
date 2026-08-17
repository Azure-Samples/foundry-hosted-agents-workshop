"""Deterministic synthetic flight generation.

Everything in a result is derived from the request: the airports fix the
great-circle distance, the distance fixes the flight durations and the price
band, and a seed built from the full argument set fixes every random-looking
choice (carrier, departure time, connecting hub, aircraft). The same query
therefore always returns the same offers, and a different query returns
different — but equally plausible — ones.

The data is fabricated. Carriers and booking platforms are fictional on
purpose so nobody mistakes a mock price for a real airline's fare, and every
payload carries ``"mock": true``.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from .airports import HUBS, AIRPORTS, Airport, distance_km, resolve_airport
from .errors import MockToolError

# Fictional carriers: name, 2-character code, alliance.
AIRLINES: tuple[tuple[str, str, str | None], ...] = (
    ("OctoAir", "Q8", "Mock Alliance"),
    ("Mockingbird Airways", "Q9", "Mock Alliance"),
    ("Sandbox Skyways", "Z7", None),
    ("Fixture Air", "Z8", "Sandbox Alliance"),
    ("Stubwing", "X9", "Sandbox Alliance"),
    ("Placeholder Airlines", "Y7", None),
)

SHORT_HAUL_AIRCRAFT: tuple[str, ...] = (
    "Airbus A319",
    "Airbus A320neo",
    "Boeing 737-800",
    "Embraer E195",
)

LONG_HAUL_AIRCRAFT: tuple[str, ...] = (
    "Airbus A330-300",
    "Airbus A350-900",
    "Boeing 777-300ER",
    "Boeing 787-9",
)

BAGGAGE_OPTIONS: tuple[str, ...] = (
    "No checked bag, Cabin bag: 1 piece(s)",
    "Cabin bag: 1 piece(s), Personal item: 1 piece(s)",
    "1 checked bag (23 kg), Cabin bag: 1 piece(s)",
    "2 checked bags (23 kg), Cabin bag: 1 piece(s)",
)

# Fictional booking platforms, matching the real response's "gate" field.
BOOKING_PLATFORMS: tuple[str, ...] = (
    "MockFares",
    "SandboxTrip",
    "DemoJet Travel",
    "TestTravel",
)

# Illustrative fixed rates against EUR. Mock money, not market data.
CURRENCY_RATES: dict[str, float] = {
    "EUR": 1.0,
    "AED": 4.00,
    "AUD": 1.64,
    "BRL": 5.90,
    "CAD": 1.48,
    "CHF": 0.94,
    "CZK": 25.20,
    "DKK": 7.46,
    "GBP": 0.85,
    "HUF": 395.0,
    "INR": 91.0,
    "ISK": 150.0,
    "JPY": 168.0,
    "MXN": 19.80,
    "NOK": 11.60,
    "PLN": 4.30,
    "SEK": 11.40,
    "SGD": 1.46,
    "THB": 39.50,
    "TRY": 38.50,
    "USD": 1.09,
    "ZAR": 20.10,
}

TRIP_CLASSES: dict[str, str] = {"Y": "economy", "C": "business"}

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
)

# Above this great-circle distance the mock stops offering non-stop options.
MAX_NONSTOP_KM = 13_500.0

# Below this one, nobody would connect: short hops are non-stop or nothing.
MIN_CONNECTION_KM = 600.0

# A connection is allowed to add a dogleg, but not an absurd one. The cap is a
# multiplier plus a slack term, so a short route can detour to a nearby hub
# while a long one stays roughly on the great circle. Without it, scoring hubs
# one at a time sent Sydney -> Auckland via Hong Kong *and* Bangkok: an 8.6x
# detour. When no hub set fits, the offer quietly comes back with fewer stops.
MAX_DETOUR_FACTOR = 1.4
MAX_DETOUR_SLACK_KM = 800.0

# Ground time between landing and taking off again on a same-day return trip.
MIN_TURNAROUND_MINUTES = 120

# Airlines load schedules about a year out; this also keeps next-day arrival
# arithmetic clear of date.max.
MAX_BOOKING_HORIZON_DAYS = 400

MOCK_NOTICE = (
    "Synthetic data from the OctoTrip Flights mock MCP server. Flights, prices, "
    "airlines, and booking links are generated from the request and are not real."
)


@dataclass(frozen=True)
class SearchQuery:
    """A validated search request."""

    origin: Airport
    destination: Airport
    departure_date: date
    return_date: date | None
    adults: int
    children: int
    infants: int
    trip_class: str
    currency: str
    locale: str

    @property
    def is_round_trip(self) -> bool:
        return self.return_date is not None

    def as_echo(self) -> dict[str, Any]:
        """Echo the normalized request, like the real server's ``query`` block."""
        return {
            "origin": self.origin.iata,
            "destination": self.destination.iata,
            "departure_date": self.departure_date.isoformat(),
            "return_date": self.return_date.isoformat() if self.return_date else None,
            "adults": self.adults,
            "children": self.children,
            "infants": self.infants,
            "trip_class": self.trip_class,
            "cabin": TRIP_CLASSES[self.trip_class],
            "currency": self.currency,
            "locale": self.locale,
        }


def _as_int(value: Any, field: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MockToolError(
            code="invalid_request",
            message=f"'{field}' must be a whole number.",
            suggestion=f"Pass an integer between {minimum} and {maximum}.",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MockToolError(
            code="invalid_request",
            message=f"'{field}' must be a whole number, got {value!r}.",
            suggestion=f"Pass an integer between {minimum} and {maximum}.",
        ) from exc
    # int() truncates, so 1.9 would quietly become one passenger.
    if parsed != value and not isinstance(value, str):
        raise MockToolError(
            code="invalid_request",
            message=f"'{field}' must be a whole number, got {value!r}.",
            suggestion=f"Pass an integer between {minimum} and {maximum}.",
        )
    if not minimum <= parsed <= maximum:
        raise MockToolError(
            code="invalid_request",
            message=f"'{field}' must be between {minimum} and {maximum}, got {parsed}.",
            suggestion=f"Pass an integer between {minimum} and {maximum}.",
        )
    return parsed


def _parse_date(value: Any, field: str, today: date) -> date:
    if not isinstance(value, str) or not value.strip():
        raise MockToolError(
            code="invalid_date",
            message=f"'{field}' is required.",
            suggestion="Use YYYY-MM-DD, for example '2026-08-01'.",
        )

    cleaned = value.strip().replace(",", "")
    keyword = cleaned.casefold()
    if keyword == "today":
        return today
    if keyword == "tomorrow":
        return today + timedelta(days=1)

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue

    raise MockToolError(
        code="invalid_date",
        message=f"Could not read '{value}' as a date for '{field}'.",
        suggestion="Use YYYY-MM-DD (for example '2026-08-01'), DD.MM.YYYY, 'today', or 'tomorrow'.",
    )


def _normalize(arguments: dict[str, Any], today: date) -> SearchQuery:
    if not isinstance(arguments, dict):
        raise MockToolError(
            code="invalid_request",
            message="Tool arguments must be an object.",
            suggestion="Send origin, destination, and departure_date as named arguments.",
        )

    origin = resolve_airport(arguments.get("origin"), "origin")
    destination = resolve_airport(arguments.get("destination"), "destination")
    if origin.iata == destination.iata:
        raise MockToolError(
            code="no_results",
            message=f"Origin and destination are the same airport ({origin.iata}).",
            suggestion="Pick two different airports.",
        )

    departure_date = _parse_date(arguments.get("departure_date"), "departure_date", today)
    if departure_date < today:
        raise MockToolError(
            code="invalid_date",
            message=f"departure_date {departure_date.isoformat()} is in the past.",
            suggestion=f"Search a date on or after {today.isoformat()}.",
        )
    # Real airlines don't sell this far out either, and it keeps date arithmetic
    # for next-day arrivals well clear of date.max.
    latest = today + timedelta(days=MAX_BOOKING_HORIZON_DAYS)
    if departure_date > latest:
        raise MockToolError(
            code="invalid_date",
            message=f"departure_date {departure_date.isoformat()} is too far ahead.",
            suggestion=f"Schedules only go out to {latest.isoformat()}.",
        )

    return_date: date | None = None
    if arguments.get("return_date"):
        return_date = _parse_date(arguments.get("return_date"), "return_date", today)
        if return_date < departure_date:
            raise MockToolError(
                code="invalid_date",
                message="return_date is before departure_date.",
                suggestion="Set return_date on or after departure_date, or omit it for a one-way search.",
            )
        if return_date > latest:
            raise MockToolError(
                code="invalid_date",
                message=f"return_date {return_date.isoformat()} is too far ahead.",
                suggestion=f"Schedules only go out to {latest.isoformat()}.",
            )

    trip_class = str(arguments.get("trip_class") or "Y").strip().upper()
    if trip_class not in TRIP_CLASSES:
        raise MockToolError(
            code="invalid_request",
            message=f"Unsupported trip_class {trip_class!r}.",
            suggestion="Use 'Y' for economy or 'C' for business.",
        )

    currency = str(arguments.get("currency") or "EUR").strip().upper()
    if currency not in CURRENCY_RATES:
        raise MockToolError(
            code="invalid_request",
            message=f"Unsupported currency {currency!r}.",
            suggestion="Use one of: " + ", ".join(sorted(CURRENCY_RATES)) + ".",
        )

    return SearchQuery(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=_as_int(arguments.get("adults"), "adults", 1, 9, 1),
        children=_as_int(arguments.get("children"), "children", 0, 9, 0),
        infants=_as_int(arguments.get("infants"), "infants", 0, 9, 0),
        trip_class=trip_class,
        currency=currency,
        locale=str(arguments.get("locale") or "en").strip() or "en",
    )


def _seeded_rng(query: SearchQuery, salt: str) -> random.Random:
    """Seed from the request so identical queries replay identical results.

    ``hash()`` is salted per process, so hash the request explicitly instead.
    Currency and locale are left out on purpose: they only change how an
    itinerary is presented, so switching them reprices the same flights instead
    of returning a different set of them.
    """
    fingerprint = "|".join(
        [
            query.origin.iata,
            query.destination.iata,
            query.departure_date.isoformat(),
            query.return_date.isoformat() if query.return_date else "-",
            str(query.adults),
            str(query.children),
            str(query.infants),
            query.trip_class,
            salt,
        ]
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _flight_minutes(kilometres: float) -> int:
    """Block time for one leg: taxi/climb overhead plus cruise at ~780 km/h."""
    return int(round(30 + kilometres / 13.0))


def _path_km(route: list[Airport]) -> float:
    """Great-circle length of a whole multi-leg route."""
    return sum(distance_km(a, b) for a, b in zip(route, route[1:]))


def _pick_hubs(rng: random.Random, query: SearchQuery, count: int) -> list[Airport]:
    """Choose connecting airports that keep the itinerary geographically sane.

    Candidates are scored on how much detour they add, plus a penalty for
    splitting the trip unevenly — that keeps a long-haul connection near the
    middle of the route instead of one hop from the destination.

    Scoring one hub at a time says nothing about how a *pair* of them combines,
    so the chosen set is measured end to end against the detour cap. If it
    doesn't fit, drop a stop and try again rather than sell a zig-zag.
    """
    direct = distance_km(query.origin, query.destination)
    max_path = direct * MAX_DETOUR_FACTOR + MAX_DETOUR_SLACK_KM
    excluded = {query.origin.iata, query.destination.iata}
    candidates = [AIRPORTS[code] for code in HUBS if code not in excluded]

    def score(hub: Airport) -> float:
        first = distance_km(query.origin, hub)
        second = distance_km(hub, query.destination)
        return (first + second - direct) + 0.35 * abs(first - second)

    candidates.sort(key=score)
    shortlist = candidates[: max(count + 3, 5)]

    while count > 0:
        chosen = rng.sample(shortlist, count)
        chosen.sort(key=lambda hub: distance_km(query.origin, hub))
        if _path_km([query.origin, *chosen, query.destination]) <= max_path:
            return chosen
        count -= 1
    return []


def _aircraft(rng: random.Random, kilometres: float) -> str:
    pool = LONG_HAUL_AIRCRAFT if kilometres > 3500 else SHORT_HAUL_AIRCRAFT
    return rng.choice(pool)


def _build_journey(
    rng: random.Random,
    route: list[Airport],
    travel_date: date,
    airline: tuple[str, str, str | None],
    departure_hour: int,
    departure_minute: int,
) -> tuple[dict[str, Any], int]:
    """Fly a route leg by leg and render local departure/arrival clocks."""
    airline_name, airline_code, _ = airline
    origin, destination = route[0], route[-1]

    local_departure = datetime.combine(travel_date, datetime.min.time()).replace(
        hour=departure_hour, minute=departure_minute
    )
    current_utc = local_departure - timedelta(hours=origin.utc_offset_hours)
    first_departure_utc = current_utc

    legs: list[dict[str, Any]] = []
    for index in range(len(route) - 1):
        leg_origin, leg_destination = route[index], route[index + 1]
        leg_km = distance_km(leg_origin, leg_destination)
        leg_minutes = _flight_minutes(leg_km)
        arrival_utc = current_utc + timedelta(minutes=leg_minutes)

        leg_departure_local = current_utc + timedelta(hours=leg_origin.utc_offset_hours)
        leg_arrival_local = arrival_utc + timedelta(hours=leg_destination.utc_offset_hours)

        legs.append(
            {
                "flight_number": f"{airline_code}{rng.randint(100, 1999)}",
                "carrier": airline_name,
                "aircraft": _aircraft(rng, leg_km),
                "departure": leg_origin.iata,
                "arrival": leg_destination.iata,
                "departure_time": leg_departure_local.strftime("%H:%M"),
                "arrival_time": leg_arrival_local.strftime("%H:%M"),
                "departure_date": leg_departure_local.date().isoformat(),
                "arrival_date": leg_arrival_local.date().isoformat(),
                "duration_minutes": leg_minutes,
            }
        )

        current_utc = arrival_utc
        if index < len(route) - 2:
            current_utc += timedelta(minutes=rng.randint(55, 190))

    total_minutes = int((current_utc - first_departure_utc).total_seconds() // 60)
    arrival_local = current_utc + timedelta(hours=destination.utc_offset_hours)

    journey = {
        "departure": origin.iata,
        "arrival": destination.iata,
        "departure_time": local_departure.strftime("%H:%M"),
        "arrival_time": arrival_local.strftime("%H:%M"),
        "departure_date": local_departure.date().isoformat(),
        "arrival_date": arrival_local.date().isoformat(),
        "duration_minutes": total_minutes,
        "stops": len(route) - 2,
        "legs": legs,
    }
    return journey, total_minutes


def _price(rng: random.Random, query: SearchQuery, kilometres: float, stops: int) -> float:
    """Price from distance, stops, cabin, trip type, and passenger mix."""
    base_eur = 42.0 + kilometres * 0.072
    stop_factor = {0: 1.0, 1: 0.84, 2: 0.72}[stops]
    cabin_factor = 2.9 if query.trip_class == "C" else 1.0
    trip_factor = 1.85 if query.is_round_trip else 1.0
    carrier_factor = rng.uniform(0.86, 1.32)
    passengers = query.adults + 0.78 * query.children + 0.12 * query.infants

    total_eur = base_eur * stop_factor * cabin_factor * trip_factor * carrier_factor * passengers
    return round(total_eur * CURRENCY_RATES[query.currency], 2)


def _build_offer(rng: random.Random, query: SearchQuery, stops: int) -> dict[str, Any] | None:
    """Build one offer, or ``None`` if the itinerary can't be flown as asked."""
    airline = rng.choice(AIRLINES)
    airline_name, airline_code, alliance = airline

    hubs = _pick_hubs(rng, query, stops) if stops else []
    # The detour cap may have handed back fewer hubs than requested, so the
    # route decides the stop count -- not the other way round.
    stops = len(hubs)
    outbound_route = [query.origin, *hubs, query.destination]
    departure_hour = rng.choice([6, 7, 8, 9, 10, 12, 14, 15, 17, 18, 20, 21])
    departure_minute = rng.choice([0, 5, 10, 15, 25, 30, 40, 45, 50])

    outbound, outbound_minutes = _build_journey(
        rng, outbound_route, query.departure_date, airline, departure_hour, departure_minute
    )

    inbound: dict[str, Any] | None = None
    total_minutes = outbound_minutes
    if query.return_date is not None:
        if query.return_date == query.departure_date:
            # Flying home the same day: you can't take off before you land.
            landing = datetime.combine(
                date.fromisoformat(outbound["arrival_date"]),
                time.fromisoformat(outbound["arrival_time"]),
            )
            back = landing + timedelta(minutes=MIN_TURNAROUND_MINUTES)
            if back.date() > query.return_date:
                # Too far to get there and back before the day runs out.
                return None
            return_hour, return_minute = back.hour, back.minute
        else:
            return_hour = rng.choice([7, 9, 11, 13, 16, 19, 22])
            return_minute = rng.choice([0, 10, 20, 35, 45, 55])

        inbound, inbound_minutes = _build_journey(
            rng,
            list(reversed(outbound_route)),
            query.return_date,
            airline,
            return_hour,
            return_minute,
        )
        total_minutes += inbound_minutes

    kilometres = distance_km(query.origin, query.destination)
    price = _price(rng, query, kilometres, stops)
    flight_numbers = [leg["flight_number"] for leg in outbound["legs"]]
    if inbound is not None:
        flight_numbers.extend(leg["flight_number"] for leg in inbound["legs"])

    offer: dict[str, Any] = {
        "airline": airline_name,
        "airline_code": airline_code,
        "alliance": alliance,
        "flight_numbers": flight_numbers,
        "is_direct": stops == 0,
        "stops": stops,
        "total_duration_minutes": total_minutes,
        "outbound": outbound,
        "return": inbound,
        "price": price,
        "currency": query.currency,
        "cabin": TRIP_CLASSES[query.trip_class],
        "baggage": rng.choice(BAGGAGE_OPTIONS),
        "gate": rng.choice(BOOKING_PLATFORMS),
        "booking_url": (
            "https://booking.mock.invalid/"
            f"{airline_code}/{query.origin.iata}-{query.destination.iata}/"
            f"{query.departure_date.isoformat()}?offer={rng.randrange(16**8):08x}"
        ),
        "link_type": "mock",
        "mock_notice": MOCK_NOTICE,
        "tags": ["direct"] if stops == 0 else [],
    }
    return offer


def _tag_offers(offers: list[dict[str, Any]]) -> None:
    """Add the comparison tags the real server attaches to its offers."""
    cheapest = min(offer["price"] for offer in offers)
    quickest = min(offer["total_duration_minutes"] for offer in offers)
    for offer in offers:
        tags = list(offer["tags"])
        if offer["price"] == cheapest:
            tags.append("cheapest")
        if offer["total_duration_minutes"] == quickest:
            tags.append("fastest")
        if offer["total_duration_minutes"] <= quickest * 1.25 and offer["price"] <= cheapest * 1.4:
            tags.append("convenient_ticket")
        if offer["outbound"]["arrival_date"] > offer["outbound"]["departure_date"]:
            tags.append("overnight")
        offer["tags"] = sorted(set(tags))


def search_flights(arguments: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Run a mock flight search and return an OctoTrip-shaped payload."""
    today = today or date.today()
    query = _normalize(arguments, today)
    kilometres = distance_km(query.origin, query.destination)

    plan: list[int] = []
    if kilometres <= MAX_NONSTOP_KM:
        plan.extend([0, 0])
    if kilometres > MIN_CONNECTION_KM:
        plan.extend([1, 1, 1])
    if kilometres > 2500:
        plan.extend([2, 2])
    elif kilometres > 1500:
        plan.append(2)

    offers: list[dict[str, Any]] = []
    for index, stops in enumerate(plan):
        rng = _seeded_rng(query, f"offer-{index}-{stops}")
        offer = _build_offer(rng, query, stops)
        if offer is not None:
            offers.append(offer)

    if not offers:
        raise MockToolError(
            code="no_results",
            message=(
                f"No same-day return is possible between {query.origin.iata} and "
                f"{query.destination.iata}: the outbound lands too late to fly back."
            ),
            suggestion="Ask for a later return_date, or drop it for a one-way search.",
            details={"field": "return_date"},
        )

    _tag_offers(offers)
    # Flight numbers break price ties, so the order can't wobble when a
    # currency conversion rounds two equal fares apart at the second decimal.
    offers.sort(key=lambda offer: (offer["stops"], offer["price"], offer["flight_numbers"]))

    return {
        "results": offers,
        "total": len(offers),
        # The mock never withholds results, so there is nothing more to page
        # through. Reporting a bigger inventory would invite the model to tell
        # the user about hundreds of flights that don't exist.
        "total_available": len(offers),
        "origin_resolved": query.origin.as_resolved(),
        "destination_resolved": query.destination.as_resolved(),
        "distance_km": round(kilometres),
        "validity": (
            "Mock results are stable for an identical query. The real OctoTrip server "
            "returns live offers valid for about 15 minutes."
        ),
        "query": query.as_echo(),
        "mock": True,
        "notice": MOCK_NOTICE,
    }
