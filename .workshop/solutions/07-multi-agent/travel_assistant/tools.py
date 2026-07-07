# travel_assistant/tools.py
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_framework import tool

CITY_TIME_ZONES = {
    "lisbon": "Europe/Lisbon",
    "london": "Europe/London",
    "new york": "America/New_York",
    "reykjavik": "Atlantic/Reykjavik",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "tokyo": "Asia/Tokyo",
}

MOCK_RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.09,
    "JPY": 0.0067,
    "GBP": 1.27,
}


@tool(approval_mode="never_require")
def get_weather(city: str, date: str | None = None) -> dict:
    """Return mock weather for a destination city and optional travel date.

    Use this when a traveler asks about current weather, weather for a planned
    date, packing conditions, or comparing weather across destinations. The data
    is mocked for the workshop and should be replaced with a real weather API in
    production.
    """
    requested_date = date or "today"
    return {
        "city": city,
        "date": requested_date,
        "temp_c": 22,
        "condition": "sunny",
        "note": "mock data — replace with a real API",
    }


@tool(approval_mode="never_require")
def get_local_time(city: str) -> dict:
    """Return the current local time for a city using a small city-to-time-zone map.

    Use this when a traveler asks what time it is in a destination, whether it is
    a good time to call a hotel, or how time zones compare between cities. Cities
    outside the workshop map fall back to UTC.
    """
    tz_name = CITY_TIME_ZONES.get(city.strip().lower(), "UTC")
    now = datetime.now(ZoneInfo(tz_name))
    return {
        "city": city,
        "iso_time": now.isoformat(timespec="seconds"),
        "tz": tz_name,
    }


@tool(approval_mode="never_require")
def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert a mock travel price between USD, EUR, JPY, and GBP.

    Use this for hotel prices, activity costs, meal budgets, or itinerary totals
    when the traveler asks for an approximate conversion. The exchange rates are
    static mock values for the workshop.
    """
    from_code = from_currency.upper()
    to_code = to_currency.upper()

    if from_code not in MOCK_RATES_TO_USD or to_code not in MOCK_RATES_TO_USD:
        return {
            "input": {"amount": amount, "currency": from_code},
            "output": None,
            "rate": None,
            "note": "mock data — supported currencies: USD, EUR, JPY, GBP",
        }

    amount_usd = amount * MOCK_RATES_TO_USD[from_code]
    converted = amount_usd / MOCK_RATES_TO_USD[to_code]
    rate = MOCK_RATES_TO_USD[from_code] / MOCK_RATES_TO_USD[to_code]

    return {
        "input": {"amount": amount, "currency": from_code},
        "output": {"amount": round(converted, 2), "currency": to_code},
        "rate": round(rate, 6),
        "note": "mock data",
    }
