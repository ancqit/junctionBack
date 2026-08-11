from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .database import cities, localities
from .geocoding import GeocodeResult, geocode_city_locality
from .rate_limit import RATE_LIMIT_AUTH, limiter
from .session import JunctionSession

router = APIRouter(prefix="/locations", tags=["locations"])

DEFAULT_CITIES = [
    "Mumbai",
    "Delhi",
    "Bengaluru",
    "Chennai",
    "Kolkata",
    "Hyderabad",
    "Pune",
    "Ranchi",
]

DEFAULT_LOCALITIES: dict[str, list[str]] = {
    "Mumbai": ["Andheri West", "Bandra", "Dadar", "Powai", "Colaba"],
    "Delhi": ["Connaught Place", "Karol Bagh", "Saket", "Dwarka", "Rohini"],
    "Bengaluru": ["Indiranagar", "Koramangala", "Whitefield", "Jayanagar", "MG Road"],
    "Chennai": ["T Nagar", "Anna Nagar", "Adyar", "Velachery", "Mylapore"],
    "Kolkata": ["Salt Lake", "Park Street", "Ballygunge", "Howrah", "New Town"],
    "Hyderabad": ["Banjara Hills", "Gachibowli", "Hitech City", "Secunderabad", "Madhapur"],
    "Pune": ["Koregaon Park", "Hinjewadi", "Kothrud", "Viman Nagar", "Camp"],
    "Ranchi": [
        "Lalpur",
        "Morabadi",
        "Bariatu",
        "Doranda",
        "Ashok Nagar",
        "Harmu Colony",
        "Kanke",
        "Hinoo",
        "Argora",
        "Ratu Road",
        "Main Road",
        "Namkum",
        "Dhurwa",
        "Kadru",
        "Kantatoli",
        "Kokar",
        "Pundag",
        "Tupudana",
    ],
}


class CityListResponse(BaseModel):
    cities: list[str]


class LocalityListResponse(BaseModel):
    city: str
    localities: list[str]


class AddJunctionRequest(BaseModel):
    city: str = Field(min_length=1, max_length=80)
    locality: str = Field(min_length=1, max_length=120)


class AddJunctionResponse(BaseModel):
    city: str
    locality: str
    latitude: float | None = None
    longitude: float | None = None


def _normalize(value: str) -> str:
    return value.strip()


def sync_default_locations() -> None:
    """Ensure all built-in cities and localities exist (including newly added defaults)."""
    now = datetime.now(timezone.utc)
    cities.create_index("name", unique=True)
    localities.create_index([("city", 1), ("name", 1)], unique=True)

    for name in DEFAULT_CITIES:
        cities.update_one(
            {"name": name},
            {"$setOnInsert": {"name": name, "created_at": now}},
            upsert=True,
        )

    for city, names in DEFAULT_LOCALITIES.items():
        for locality in names:
            localities.update_one(
                {"city": city, "name": locality},
                {"$setOnInsert": {"city": city, "name": locality, "created_at": now}},
                upsert=True,
            )


def seed_locations_if_empty() -> None:
    if cities.count_documents({}) == 0:
        now = datetime.now(timezone.utc)
        cities.insert_many([{"name": name, "created_at": now} for name in DEFAULT_CITIES])
        cities.create_index("name", unique=True)

    if localities.count_documents({}) == 0:
        now = datetime.now(timezone.utc)
        documents = [
            {"city": city, "name": locality, "created_at": now}
            for city, names in DEFAULT_LOCALITIES.items()
            for locality in names
        ]
        localities.insert_many(documents)
        localities.create_index([("city", 1), ("name", 1)], unique=True)

    sync_default_locations()


@router.get("/cities", response_model=CityListResponse)
def list_cities(_: JunctionSession) -> CityListResponse:
    """City dropdown for junction.today — requires a valid /session JWT."""
    seed_locations_if_empty()
    names = [document["name"] for document in cities.find().sort("name", 1)]
    return CityListResponse(cities=names)


@router.get("/localities", response_model=LocalityListResponse)
def list_localities(
    _: JunctionSession,
    city: str = Query(..., min_length=1, max_length=80),
) -> LocalityListResponse:
    """Locality dropdown for a city — requires a valid /session JWT."""
    seed_locations_if_empty()
    city_name = _normalize(city)
    if not city_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city must not be blank")

    if cities.find_one({"name": city_name}) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="City not found")

    names = [
        document["name"]
        for document in localities.find({"city": city_name}).sort("name", 1)
    ]
    return LocalityListResponse(city=city_name, localities=names)


@router.post("/add-junction", response_model=AddJunctionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_AUTH)
def add_junction(
    request: Request,
    payload: AddJunctionRequest,
    _: JunctionSession,
) -> AddJunctionResponse:
    """
    Add a city and locality after geocoding succeeds.
    Requires a valid /session JWT (junction.today guest session).
    """
    city, locality, geo = ensure_city_and_locality(payload.city, payload.locality, return_geo=True)
    return AddJunctionResponse(
        city=city,
        locality=locality,
        latitude=geo.latitude if geo else None,
        longitude=geo.longitude if geo else None,
    )


def ensure_city_and_locality(
    city: str,
    locality: str,
    *,
    return_geo: bool = False,
) -> tuple[str, str] | tuple[str, str, GeocodeResult | None]:
    """
    Normalize city/locality and add them if new.
    New localities must geocode successfully; existing ones are accepted as-is.
    """
    seed_locations_if_empty()
    cities.create_index("name", unique=True)
    localities.create_index([("city", 1), ("name", 1)], unique=True)

    city_name = _normalize(city)
    locality_name = _normalize(locality)
    if not city_name or not locality_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city and locality are required")

    existing = localities.find_one({"city": city_name, "name": locality_name})
    geo: GeocodeResult | None = None
    if existing is None:
        geo = geocode_city_locality(city_name, locality_name)
    elif return_geo and existing.get("latitude") is not None and existing.get("longitude") is not None:
        geo = GeocodeResult(
            latitude=float(existing["latitude"]),
            longitude=float(existing["longitude"]),
            display_name=str(existing.get("display_name") or f"{locality_name}, {city_name}"),
        )

    now = datetime.now(timezone.utc)
    cities.update_one(
        {"name": city_name},
        {"$setOnInsert": {"name": city_name, "created_at": now}},
        upsert=True,
    )

    locality_doc: dict = {"city": city_name, "name": locality_name, "created_at": now}
    if geo is not None:
        locality_doc["latitude"] = geo.latitude
        locality_doc["longitude"] = geo.longitude
        locality_doc["display_name"] = geo.display_name

    localities.update_one(
        {"city": city_name, "name": locality_name},
        {"$setOnInsert": locality_doc},
        upsert=True,
    )

    if return_geo:
        return city_name, locality_name, geo
    return city_name, locality_name
