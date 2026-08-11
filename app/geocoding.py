"""Geocode city + locality so only real, findable places enter the location lists."""

import os
from typing import Any

import httpx
from fastapi import HTTPException, status

NOMINATIM_URL = os.getenv(
    "GEOCODING_URL",
    "https://nominatim.openstreetmap.org/search",
).strip()
GEOCODING_USER_AGENT = os.getenv(
    "GEOCODING_USER_AGENT",
    "JunctionBackend/1.0 (https://junction.today)",
).strip()
GEOCODING_TIMEOUT_SECONDS = float(os.getenv("GEOCODING_TIMEOUT_SECONDS", "8"))
GEOCODING_COUNTRY_CODES = os.getenv("GEOCODING_COUNTRY_CODES", "in").strip() or "in"


class GeocodeResult:
    __slots__ = ("latitude", "longitude", "display_name")

    def __init__(self, latitude: float, longitude: float, display_name: str) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.display_name = display_name

    def as_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "display_name": self.display_name,
        }


def geocode_city_locality(city: str, locality: str) -> GeocodeResult:
    """
    Resolve locality within city via Nominatim.
    Raises HTTP 400 if it cannot be geocoded; 502/503 on upstream/config failures.
    """
    city_name = city.strip()
    locality_name = locality.strip()
    if not city_name or not locality_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="city and locality are required for geocoding",
        )

    query = f"{locality_name}, {city_name}, India"
    params: dict[str, str | int] = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
        "countrycodes": GEOCODING_COUNTRY_CODES,
    }
    headers = {
        "User-Agent": GEOCODING_USER_AGENT,
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=GEOCODING_TIMEOUT_SECONDS) as client:
            response = client.get(NOMINATIM_URL, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Geocoding service timed out",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach geocoding service",
        ) from exc

    if response.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Geocoding service is unavailable",
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Geocoding request was rejected",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Geocoding service returned invalid data",
        ) from exc

    if not isinstance(payload, list) or not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locality could not be geocoded; enter a real city and locality",
        )

    hit = payload[0]
    if not isinstance(hit, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locality could not be geocoded; enter a real city and locality",
        )

    try:
        latitude = float(hit["lat"])
        longitude = float(hit["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locality could not be geocoded; enter a real city and locality",
        ) from exc

    display_name = str(hit.get("display_name") or query)
    if not _result_matches_city(city_name, hit):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locality could not be geocoded within that city",
        )

    return GeocodeResult(latitude=latitude, longitude=longitude, display_name=display_name)


def _result_matches_city(city: str, hit: dict[str, Any]) -> bool:
    """Accept if the city name appears in Nominatim's address or display name."""
    needle = city.strip().lower()
    if not needle:
        return False

    display = str(hit.get("display_name") or "").lower()
    if needle in display:
        return True

    address = hit.get("address")
    if not isinstance(address, dict):
        return False

    for key in (
        "city",
        "town",
        "village",
        "municipality",
        "county",
        "state_district",
        "suburb",
        "state",
    ):
        value = address.get(key)
        if isinstance(value, str) and needle in value.lower():
            return True
    return False
