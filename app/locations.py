from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .access_control import AuthenticatedUser
from .database import cities, localities

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
def list_cities(_: AuthenticatedUser) -> CityListResponse:
    seed_locations_if_empty()
    names = [document["name"] for document in cities.find().sort("name", 1)]
    return CityListResponse(cities=names)


@router.get("/localities", response_model=LocalityListResponse)
def list_localities(
    _: AuthenticatedUser,
    city: str = Query(..., min_length=1, max_length=80),
) -> LocalityListResponse:
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
def add_junction(
    payload: AddJunctionRequest,
    _: AuthenticatedUser,
) -> AddJunctionResponse:
    """Add a city and locality to the dropdown lists (same data as shop create/update)."""
    city, locality = ensure_city_and_locality(payload.city, payload.locality)
    return AddJunctionResponse(city=city, locality=locality)


def ensure_city_and_locality(city: str, locality: str) -> tuple[str, str]:
    """Normalize city/locality and add them to the dropdown lists if they are new."""
    seed_locations_if_empty()
    cities.create_index("name", unique=True)
    localities.create_index([("city", 1), ("name", 1)], unique=True)

    city_name = _normalize(city)
    locality_name = _normalize(locality)
    if not city_name or not locality_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city and locality are required")

    now = datetime.now(timezone.utc)
    cities.update_one(
        {"name": city_name},
        {"$setOnInsert": {"name": city_name, "created_at": now}},
        upsert=True,
    )
    localities.update_one(
        {"city": city_name, "name": locality_name},
        {"$setOnInsert": {"city": city_name, "name": locality_name, "created_at": now}},
        upsert=True,
    )
    return city_name, locality_name
