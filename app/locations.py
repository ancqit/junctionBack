from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from .database import cities, localities

router = APIRouter(prefix="/locations", tags=["locations"])

DEFAULT_CITIES = ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Hyderabad", "Pune"]

DEFAULT_LOCALITIES: dict[str, list[str]] = {
    "Mumbai": ["Andheri West", "Bandra", "Dadar", "Powai", "Colaba"],
    "Delhi": ["Connaught Place", "Karol Bagh", "Saket", "Dwarka", "Rohini"],
    "Bengaluru": ["Indiranagar", "Koramangala", "Whitefield", "Jayanagar", "MG Road"],
    "Chennai": ["T Nagar", "Anna Nagar", "Adyar", "Velachery", "Mylapore"],
    "Kolkata": ["Salt Lake", "Park Street", "Ballygunge", "Howrah", "New Town"],
    "Hyderabad": ["Banjara Hills", "Gachibowli", "Hitech City", "Secunderabad", "Madhapur"],
    "Pune": ["Koregaon Park", "Hinjewadi", "Kothrud", "Viman Nagar", "Camp"],
}


class CityListResponse(BaseModel):
    cities: list[str]


class LocalityListResponse(BaseModel):
    city: str
    localities: list[str]


def _normalize(value: str) -> str:
    return value.strip()


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


@router.get("/cities", response_model=CityListResponse)
def list_cities() -> CityListResponse:
    seed_locations_if_empty()
    names = [document["name"] for document in cities.find().sort("name", 1)]
    return CityListResponse(cities=names)


@router.get("/localities", response_model=LocalityListResponse)
def list_localities(city: str = Query(..., min_length=1, max_length=80)) -> LocalityListResponse:
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


def validate_city_and_locality(city: str, locality: str) -> tuple[str, str]:
    seed_locations_if_empty()
    city_name = _normalize(city)
    locality_name = _normalize(locality)
    if not city_name or not locality_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="city and locality are required")

    if cities.find_one({"name": city_name}) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"City '{city_name}' is not in the allowed list")

    if localities.find_one({"city": city_name, "name": locality_name}) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Locality '{locality_name}' is not available for city '{city_name}'",
        )
    return city_name, locality_name
