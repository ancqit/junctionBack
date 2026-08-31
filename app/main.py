import os
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .access_control import AuthenticatedUser
from .cors_config import load_cors_origins
from .database import items
from .admin import router as admin_router
from .digilocker import router as digilocker_router
from .employees import router as employees_router
from .locations import router as locations_router
from .login import router as login_router
from .orders import router as orders_router
from .plans import router as plans_router
from .plan_applications import router as plan_applications_router
from .products import router as products_router
from .product_bucket import router as product_bucket_router
from .profile import notices_router, router as profile_router
from .queries import router as queries_router
from .rate_limit import limiter
from .session import router as session_router
from .shops import router as shops_router
from .shop_payments import router as shop_payments_router
from .terms import router as terms_router
from .waitlist import router as waitlist_router
from .whatsapp_otp import router as whatsapp_otp_router

_openapi_enabled = os.getenv("OPENAPI_ENABLED", "true").lower() in {"1", "true", "yes"}

app = FastAPI(
    title="Junction Backend",
    description="A small CRUD API backed by MongoDB.",
    version="1.0.0",
    docs_url="/docs" if _openapi_enabled else None,
    redoc_url="/redoc" if _openapi_enabled else None,
    openapi_url="/openapi.json" if _openapi_enabled else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=load_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_routers = (
    login_router,
    whatsapp_otp_router,
    session_router,
    digilocker_router,
    profile_router,
    notices_router,
    products_router,
    product_bucket_router,
    employees_router,
    orders_router,
    queries_router,
    locations_router,
    plans_router,
    plan_applications_router,
    waitlist_router,
    terms_router,
    admin_router,
    shops_router,
    shop_payments_router,
)
for _router in _routers:
    app.include_router(_router)
    app.include_router(_router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for Render / load balancers. No auth. Prefer this over /docs."""
    return {"status": "ok"}


class ItemCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float = Field(ge=0)


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float | None = Field(default=None, ge=0)


class Item(ItemCreate):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime
    updated_at: datetime


def serialize_item(document: dict) -> Item:
    return Item(
        id=str(document["_id"]),
        store_id=document["store_id"],
        name=document["name"],
        description=document.get("description"),
        price=document["price"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def object_id(item_id: str) -> ObjectId:
    if not ObjectId.is_valid(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return ObjectId(item_id)


@app.post("/items", response_model=Item, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, _: AuthenticatedUser) -> Item:
    now = datetime.now(timezone.utc)
    document = {**payload.model_dump(), "created_at": now, "updated_at": now}
    result = items.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_item(document)


@app.get("/items/{item_id}", response_model=Item)
def read_item(item_id: str, _: AuthenticatedUser) -> Item:
    document = items.find_one({"_id": object_id(item_id)})
    if document is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return serialize_item(document)


@app.put("/items/{item_id}", response_model=Item)
def update_item(item_id: str, payload: ItemUpdate, _: AuthenticatedUser) -> Item:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    changes["updated_at"] = datetime.now(timezone.utc)
    document = items.find_one_and_update(
        {"_id": object_id(item_id)},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return serialize_item(document)


@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, _: AuthenticatedUser) -> Response:
    result = items.delete_one({"_id": object_id(item_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
