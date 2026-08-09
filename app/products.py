from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import products
from .utils import parse_object_id

router = APIRouter(prefix="/products", tags=["products"])


class ProductStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    discontinued = "discontinued"


class ProductCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    stock_quantity: int = Field(default=0, ge=0)
    unit: str = Field(default="piece", min_length=1, max_length=32)
    status: ProductStatus = ProductStatus.active
    tags: list[str] = Field(default_factory=list, max_length=20)
    image_url: HttpUrl | None = None
    barcode: str | None = Field(default=None, max_length=64)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    low_stock_threshold: int | None = Field(default=None, ge=0)

    @field_validator("sku", "name", "category", "unit")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return [tag.strip() for tag in value if tag.strip()]


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    stock_quantity: int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    status: ProductStatus | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    image_url: HttpUrl | None = None
    barcode: str | None = Field(default=None, max_length=64)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    low_stock_threshold: int | None = Field(default=None, ge=0)

    @field_validator("sku", "name", "category", "unit")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [tag.strip() for tag in value if tag.strip()]


class Product(ProductCreate):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime
    updated_at: datetime


def serialize_product(document: dict) -> Product:
    return Product(
        id=str(document["_id"]),
        store_id=document["store_id"],
        sku=document["sku"],
        name=document["name"],
        description=document.get("description"),
        category=document["category"],
        price=document["price"],
        cost_price=document.get("cost_price"),
        currency=document.get("currency", "INR"),
        stock_quantity=document.get("stock_quantity", 0),
        unit=document.get("unit", "piece"),
        status=document.get("status", ProductStatus.active.value),
        tags=document.get("tags", []),
        image_url=document.get("image_url"),
        barcode=document.get("barcode"),
        tax_rate=document.get("tax_rate"),
        low_stock_threshold=document.get("low_stock_threshold"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


@router.get("", response_model=list[Product])
def list_products(store_id: str | None = Query(default=None, min_length=1, max_length=80)) -> list[Product]:
    query = {"store_id": store_id} if store_id else {}
    documents = products.find(query).sort("created_at", -1)
    return [serialize_product(document) for document in documents]


@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate) -> Product:
    products.create_index([("store_id", 1), ("sku", 1)], unique=True)
    now = datetime.now(timezone.utc)
    document = {**payload.model_dump(mode="json"), "created_at": now, "updated_at": now}
    try:
        result = products.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A product with this SKU already exists for the store")
    document["_id"] = result.inserted_id
    return serialize_product(document)


@router.put("/{product_id}", response_model=Product)
def update_product(product_id: str, payload: ProductUpdate) -> Product:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    changes["updated_at"] = datetime.now(timezone.utc)
    try:
        document = products.find_one_and_update(
            {"_id": parse_object_id(product_id, "Product")},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A product with this SKU already exists for the store")
    if document is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_product(document)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: str) -> Response:
    result = products.delete_one({"_id": parse_object_id(product_id, "Product")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
