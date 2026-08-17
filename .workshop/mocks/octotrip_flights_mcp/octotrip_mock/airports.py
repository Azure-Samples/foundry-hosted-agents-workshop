"""Airport reference data and the resolver the mock ``search`` tool uses.

Coordinates are real, which is what makes the generated data track the request:
flight durations come from the great-circle distance between the two airports,
and prices are derived from that distance. Resolution accepts an IATA code, a
city name, or an airport name — and raises the same structured errors the real
OctoTrip server returns when a query is unknown or ambiguous.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .errors import MockToolError

IATA_CODE_RE = re.compile(r"^[A-Za-z]{3}$")


@dataclass(frozen=True)
class Airport:
    """One airport: identity plus the coordinates the generator needs."""

    iata: str
    name: str
    city: str
    country_code: str
    latitude: float
    longitude: float
    utc_offset_hours: int

    def as_resolved(self) -> dict[str, str]:
        """The ``*_resolved`` block the tool result reports back."""
        return {
            "iata": self.iata,
            "name": self.name,
            "city": self.city,
            "country_code": self.country_code,
        }


# iata, name, city, country, latitude, longitude, standard-time UTC offset.
#
# The offset is stored rather than derived from longitude: political time zones
# don't follow meridians, so round(longitude / 15) put Reykjavik and Marrakech
# two hours out and most of western Europe one hour out. Whole hours only (India
# and similar half-hour zones are rounded), and standard time throughout -- the
# mock has no DST, which the README says out loud.
_AIRPORT_ROWS: tuple[tuple[str, str, str, str, float, float, int], ...] = (
    ("AKL", "Auckland Airport", "Auckland", "NZ", -37.0082, 174.7850, 12),
    ("AMS", "Amsterdam Airport Schiphol", "Amsterdam", "NL", 52.3105, 4.7683, 1),
    ("ARN", "Stockholm Arlanda Airport", "Stockholm", "SE", 59.6519, 17.9186, 1),
    ("ATH", "Athens International Airport", "Athens", "GR", 37.9364, 23.9445, 2),
    ("ATL", "Hartsfield-Jackson Atlanta International Airport", "Atlanta", "US", 33.6407, -84.4277, -5),
    ("BCN", "Josep Tarradellas Barcelona-El Prat Airport", "Barcelona", "ES", 41.2974, 2.0833, 1),
    ("BKK", "Suvarnabhumi Airport", "Bangkok", "TH", 13.6900, 100.7501, 7),
    ("BOM", "Chhatrapati Shivaji Maharaj International Airport", "Mumbai", "IN", 19.0896, 72.8656, 5),
    ("BOS", "Boston Logan International Airport", "Boston", "US", 42.3656, -71.0096, -5),
    ("BRU", "Brussels Airport", "Brussels", "BE", 50.9014, 4.4844, 1),
    ("BUD", "Budapest Ferenc Liszt International Airport", "Budapest", "HU", 47.4369, 19.2556, 1),
    ("CDG", "Paris Charles de Gaulle Airport", "Paris", "FR", 49.0097, 2.5479, 1),
    ("CPH", "Copenhagen Airport", "Copenhagen", "DK", 55.6180, 12.6560, 1),
    ("CPT", "Cape Town International Airport", "Cape Town", "ZA", -33.9715, 18.6021, 2),
    ("DEL", "Indira Gandhi International Airport", "Delhi", "IN", 28.5562, 77.1000, 5),
    ("DFW", "Dallas/Fort Worth International Airport", "Dallas", "US", 32.8998, -97.0403, -6),
    ("DOH", "Hamad International Airport", "Doha", "QA", 25.2731, 51.6081, 3),
    ("DUB", "Dublin Airport", "Dublin", "IE", 53.4213, -6.2701, 0),
    ("DXB", "Dubai International Airport", "Dubai", "AE", 25.2532, 55.3657, 4),
    ("EWR", "Newark Liberty International Airport", "Newark", "US", 40.6895, -74.1745, -5),
    ("EZE", "Ministro Pistarini International Airport", "Buenos Aires", "AR", -34.8222, -58.5358, -3),
    ("FCO", "Rome Fiumicino Airport", "Rome", "IT", 41.8003, 12.2389, 1),
    ("FRA", "Frankfurt Airport", "Frankfurt am Main", "DE", 50.0379, 8.5622, 1),
    ("GRU", "Sao Paulo/Guarulhos International Airport", "Sao Paulo", "BR", -23.4356, -46.4731, -3),
    ("HEL", "Helsinki-Vantaa Airport", "Helsinki", "FI", 60.3172, 24.9633, 2),
    ("HKG", "Hong Kong International Airport", "Hong Kong", "HK", 22.3080, 113.9185, 8),
    ("HND", "Tokyo Haneda Airport", "Tokyo", "JP", 35.5494, 139.7798, 9),
    ("ICN", "Incheon International Airport", "Seoul", "KR", 37.4602, 126.4407, 9),
    ("IST", "Istanbul Airport", "Istanbul", "TR", 41.2753, 28.7519, 3),
    ("JFK", "John F. Kennedy International Airport", "New York", "US", 40.6413, -73.7781, -5),
    ("JNB", "O. R. Tambo International Airport", "Johannesburg", "ZA", -26.1392, 28.2460, 2),
    ("KEF", "Keflavik International Airport", "Reykjavik", "IS", 63.9850, -22.6056, 0),
    ("LAX", "Los Angeles International Airport", "Los Angeles", "US", 33.9416, -118.4085, -8),
    ("LGA", "LaGuardia Airport", "New York", "US", 40.7769, -73.8740, -5),
    ("LGW", "London Gatwick Airport", "London", "GB", 51.1537, -0.1821, 0),
    ("LHR", "London Heathrow Airport", "London", "GB", 51.4700, -0.4543, 0),
    ("LIS", "Lisbon Humberto Delgado Airport", "Lisbon", "PT", 38.7756, -9.1354, 0),
    ("MAD", "Adolfo Suarez Madrid-Barajas Airport", "Madrid", "ES", 40.4719, -3.5626, 1),
    ("MEL", "Melbourne Airport", "Melbourne", "AU", -37.6690, 144.8410, 10),
    ("MEX", "Mexico City International Airport", "Mexico City", "MX", 19.4363, -99.0721, -6),
    ("MIA", "Miami International Airport", "Miami", "US", 25.7959, -80.2870, -5),
    ("MUC", "Munich Airport", "Munich", "DE", 48.3538, 11.7861, 1),
    ("MXP", "Milan Malpensa Airport", "Milan", "IT", 45.6306, 8.7281, 1),
    ("NBO", "Jomo Kenyatta International Airport", "Nairobi", "KE", -1.3192, 36.9278, 3),
    ("NRT", "Tokyo Narita International Airport", "Tokyo", "JP", 35.7720, 140.3929, 9),
    ("OPO", "Porto Francisco Sa Carneiro Airport", "Porto", "PT", 41.2481, -8.6814, 0),
    ("ORD", "Chicago O'Hare International Airport", "Chicago", "US", 41.9742, -87.9073, -6),
    ("ORY", "Paris Orly Airport", "Paris", "FR", 48.7233, 2.3794, 1),
    ("OSL", "Oslo Gardermoen Airport", "Oslo", "NO", 60.1976, 11.1004, 1),
    ("PEK", "Beijing Capital International Airport", "Beijing", "CN", 40.0799, 116.6031, 8),
    ("PRG", "Vaclav Havel Airport Prague", "Prague", "CZ", 50.1008, 14.2600, 1),
    ("PVG", "Shanghai Pudong International Airport", "Shanghai", "CN", 31.1443, 121.8083, 8),
    ("RAK", "Marrakech Menara Airport", "Marrakech", "MA", 31.6069, -8.0363, 1),
    ("SEA", "Seattle-Tacoma International Airport", "Seattle", "US", 47.4502, -122.3088, -8),
    ("SFO", "San Francisco International Airport", "San Francisco", "US", 37.6213, -122.3790, -8),
    ("SIN", "Singapore Changi Airport", "Singapore", "SG", 1.3644, 103.9915, 8),
    ("SYD", "Sydney Kingsford Smith Airport", "Sydney", "AU", -33.9399, 151.1753, 10),
    ("TPE", "Taiwan Taoyuan International Airport", "Taipei", "TW", 25.0777, 121.2328, 8),
    ("VIE", "Vienna International Airport", "Vienna", "AT", 48.1103, 16.5697, 1),
    ("WAW", "Warsaw Chopin Airport", "Warsaw", "PL", 52.1657, 20.9671, 1),
    ("YVR", "Vancouver International Airport", "Vancouver", "CA", 49.1967, -123.1815, -8),
    ("YYZ", "Toronto Pearson International Airport", "Toronto", "CA", 43.6777, -79.6248, -5),
    ("ZRH", "Zurich Airport", "Zurich", "CH", 47.4647, 8.5492, 1),
)

AIRPORTS: dict[str, Airport] = {row[0]: Airport(*row) for row in _AIRPORT_ROWS}

# Metro areas whose common name doesn't match every airport's city field, and
# which have no single obvious main airport. These stay ambiguous on purpose:
# the real server asks you to choose, and so does the mock.
METRO_AREAS: dict[str, tuple[str, ...]] = {
    "new york": ("JFK", "LGA", "EWR"),
    "new york city": ("JFK", "LGA", "EWR"),
    "nyc": ("JFK", "LGA", "EWR"),
}

# Cities served by several airports where one is clearly the principal one.
# Asking for "Tokyo" should get you Haneda, not a disambiguation prompt.
PRIMARY_AIRPORTS: dict[str, str] = {
    "london": "LHR",
    "milan": "MXP",
    "paris": "CDG",
    "rome": "FCO",
    "seoul": "ICN",
    "tokyo": "HND",
}

# Hubs the mock routes connecting itineraries through. Spread across regions so
# a connection is never absurdly far off the direct path.
HUBS: tuple[str, ...] = (
    "AMS",
    "ATL",
    "BKK",
    "CDG",
    "DFW",
    "DOH",
    "DXB",
    "FRA",
    "GRU",
    "HKG",
    "ICN",
    "IST",
    "JFK",
    "LAX",
    "LHR",
    "MAD",
    "MUC",
    "NBO",
    "ORD",
    "PVG",
    "SFO",
    "SIN",
    "YYZ",
    "ZRH",
)


def distance_km(origin: Airport, destination: Airport) -> float:
    """Great-circle distance between two airports, in kilometres."""
    earth_radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (origin.latitude, origin.longitude, destination.latitude, destination.longitude),
    )
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _disambiguation_error(query: str, candidates: list[Airport]) -> MockToolError:
    options = [airport.as_resolved() for airport in candidates]
    names = ", ".join(f"{airport.iata} ({airport.name})" for airport in candidates)
    return MockToolError(
        code="disambiguation_needed",
        message=f"'{query}' matches several airports: {names}.",
        suggestion="Pick one airport and retry the search with its IATA code.",
        details={"options": options},
    )


def resolve_airport(query: str, field: str) -> Airport:
    """Resolve an IATA code, city, or airport name to a single airport."""
    if not isinstance(query, str) or not query.strip():
        raise MockToolError(
            code="invalid_request",
            message=f"'{field}' is required.",
            suggestion="Pass a departure city, airport name, or IATA code.",
        )

    cleaned = query.strip()
    normalized = cleaned.casefold()

    # An IATA-shaped string is only ever a code, so an unknown one is a typo --
    # inventing an airport for it would answer a mistake with a confident lie.
    if IATA_CODE_RE.match(cleaned):
        known = AIRPORTS.get(cleaned.upper())
        if known:
            return known
        raise MockToolError(
            code="airport_not_found",
            message=f"'{cleaned.upper()}' is not an airport this mock knows.",
            suggestion="Check the IATA code, or pass a city name such as 'Lisbon'.",
            details={"field": field},
        )

    metro = METRO_AREAS.get(normalized)
    if metro:
        candidates = [AIRPORTS[code] for code in metro]
        if len(candidates) == 1:
            return candidates[0]
        raise _disambiguation_error(cleaned, candidates)

    primary = PRIMARY_AIRPORTS.get(normalized)
    if primary:
        return AIRPORTS[primary]

    by_city = [airport for airport in AIRPORTS.values() if airport.city.casefold() == normalized]
    if len(by_city) == 1:
        return by_city[0]
    if by_city:
        raise _disambiguation_error(cleaned, sorted(by_city, key=lambda airport: airport.iata))

    # Whole-word match, so "port" doesn't half-match every "... Airport" and a
    # stray fragment can't quietly resolve to the one name that contains it.
    by_name = [
        airport
        for airport in AIRPORTS.values()
        if re.search(rf"\b{re.escape(normalized)}\b", airport.name.casefold())
    ]
    if len(by_name) == 1:
        return by_name[0]
    if by_name:
        raise _disambiguation_error(cleaned, sorted(by_name, key=lambda airport: airport.iata))

    raise MockToolError(
        code="airport_not_found",
        message=f"Could not resolve '{cleaned}' to an airport.",
        suggestion="Try a three-letter IATA code (for example 'CPH'), a city name, or an airport name.",
        details={"field": field},
    )
