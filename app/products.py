from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import products
from .product_images import (
    delete_product_image,
    fetch_image_from_cdn,
    get_product_image,
    save_product_image,
    validate_image_upload,
)
from .utils import parse_object_id

router = APIRouter(prefix="/products", tags=["products"])


class ProductStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    discontinued = "discontinued"


class ProductImageSource(str, Enum):
    cdn = "cdn"
    query = "query"
    upload = "upload"


class ProductImage(BaseModel):
    source: ProductImageSource
    cdn: HttpUrl | None = None
    stored_image_id: str | None = None
    content_type: str | None = None
    filename: str | None = None


class ProductImageCdnRequest(BaseModel):
    cdn: HttpUrl


class ProductImageUseRequest(BaseModel):
    cdn: HttpUrl


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
    image_cdn: HttpUrl | None = None
    image: ProductImage | None = None
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

    @model_validator(mode="after")
    def normalize_image_fields(self) -> "ProductCreate":
        if self.image is None and self.image_cdn is not None:
            self.image = ProductImage(source=ProductImageSource.cdn, cdn=self.image_cdn)
        elif self.image is None and self.image_url is not None:
            self.image = ProductImage(source=ProductImageSource.cdn, cdn=self.image_url)
            self.image_cdn = self.image_url
        elif self.image is not None and self.image.cdn is not None and self.image_cdn is None:
            self.image_cdn = self.image.cdn
        return self


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
    image_cdn: HttpUrl | None = None
    image: ProductImage | None = None
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

    @model_validator(mode="after")
    def normalize_image_fields(self) -> "ProductUpdate":
        if self.image is None and self.image_cdn is not None:
            self.image = ProductImage(source=ProductImageSource.cdn, cdn=self.image_cdn)
        elif self.image is None and self.image_url is not None:
            self.image = ProductImage(source=ProductImageSource.cdn, cdn=self.image_url)
            self.image_cdn = self.image_url
        elif self.image is not None and self.image.cdn is not None and self.image_cdn is None:
            self.image_cdn = self.image.cdn
        return self


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    store_id: str
    sku: str
    name: str
    description: str | None = None
    category: str
    price: float
    cost_price: float | None = None
    currency: str = "INR"
    stock_quantity: int = 0
    unit: str = "piece"
    status: ProductStatus = ProductStatus.active
    tags: list[str] = Field(default_factory=list)
    image_cdn: HttpUrl | None = None
    image: ProductImage | None = None
    image_url: HttpUrl | None = None
    barcode: str | None = None
    tax_rate: float | None = None
    low_stock_threshold: int | None = None
    created_at: datetime
    updated_at: datetime


def build_image_payload(image: ProductImage | None, image_cdn: HttpUrl | None) -> dict | None:
    if image is None:
        return None
    payload = image.model_dump(mode="json")
    if image.cdn is None and image_cdn is not None:
        payload["cdn"] = str(image_cdn)
    return payload


def serialize_product(document: dict) -> Product:
    image_data = document.get("image")
    image = ProductImage(**image_data) if image_data else None
    image_cdn = document.get("image_cdn") or (image.cdn if image else None) or document.get("image_url")
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
        image_cdn=image_cdn,
        image=image,
        image_url=image_cdn,
        barcode=document.get("barcode"),
        tax_rate=document.get("tax_rate"),
        low_stock_threshold=document.get("low_stock_threshold"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def product_document_from_payload(payload: ProductCreate) -> dict:
    document = payload.model_dump(mode="json", exclude={"image", "image_url"})
    document["image"] = build_image_payload(payload.image, payload.image_cdn)
    document["image_url"] = str(payload.image_cdn) if payload.image_cdn else None
    return document


def apply_image_update(existing: dict, image: ProductImage) -> dict:
    previous_image = existing.get("image") or {}
    if previous_image.get("stored_image_id") and previous_image.get("stored_image_id") != image.stored_image_id:
        delete_product_image(previous_image.get("stored_image_id"))
    return image.model_dump(mode="json")


@router.get("/images/{stored_image_id}")
def get_stored_product_image(stored_image_id: str) -> StreamingResponse:
    stream, content_type, filename = get_product_image(stored_image_id)
    return StreamingResponse(stream, media_type=content_type, headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("", response_model=list[Product])
def list_products(store_id: str | None = Query(default=None, min_length=1, max_length=80)) -> list[Product]:
    query = {"store_id": store_id} if store_id else {}
    documents = products.find(query).sort("created_at", -1)
    return [serialize_product(document) for document in documents]


@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate) -> Product:
    products.create_index([("store_id", 1), ("sku", 1)], unique=True)
    now = datetime.now(timezone.utc)
    document = {**product_document_from_payload(payload), "created_at": now, "updated_at": now}
    try:
        result = products.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A product with this SKU already exists for the store")
    document["_id"] = result.inserted_id
    return serialize_product(document)


@router.post("/{product_id}/image/cdn", response_model=Product)
def set_product_image_cdn(product_id: str, payload: ProductImageCdnRequest) -> Product:
    image = ProductImage(source=ProductImageSource.cdn, cdn=payload.cdn)
    return update_product_image(product_id, image, image_cdn=str(payload.cdn))


@router.post("/{product_id}/image/use", response_model=Product)
async def use_product_image_from_cdn(product_id: str, payload: ProductImageUseRequest) -> Product:
    product = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    contents, content_type, filename = await fetch_image_from_cdn(str(payload.cdn))
    stored = save_product_image(
        contents,
        content_type=content_type,
        filename=filename,
        source=ProductImageSource.query.value,
        source_cdn=str(payload.cdn),
        product_id=product_id,
        store_id=product.get("store_id"),
    )
    image = ProductImage(
        source=ProductImageSource.query,
        cdn=payload.cdn,
        stored_image_id=str(stored.file_id),
        content_type=stored.content_type,
        filename=stored.filename,
    )
    return update_product_image(product_id, image, image_cdn=str(payload.cdn))


@router.post("/{product_id}/image/upload", response_model=Product)
async def upload_product_image(product_id: str, file: UploadFile = File(...)) -> Product:
    product = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    contents = await file.read()
    content_type = validate_image_upload(file, contents)
    stored = save_product_image(
        contents,
        content_type=content_type,
        filename=file.filename or "upload.jpg",
        source=ProductImageSource.upload.value,
        product_id=product_id,
        store_id=product.get("store_id"),
    )
    image = ProductImage(
        source=ProductImageSource.upload,
        stored_image_id=str(stored.file_id),
        content_type=stored.content_type,
        filename=stored.filename,
    )
    return update_product_image(product_id, image)


def update_product_image(product_id: str, image: ProductImage, image_cdn: str | None = None) -> Product:
    existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    previous_image = existing.get("image") or {}
    if previous_image.get("stored_image_id") and previous_image.get("stored_image_id") != image.stored_image_id:
        delete_product_image(previous_image.get("stored_image_id"))

    changes = {
        "image": image.model_dump(mode="json"),
        "image_cdn": image_cdn or (str(image.cdn) if image.cdn else None),
        "image_url": image_cdn or (str(image.cdn) if image.cdn else None),
        "updated_at": datetime.now(timezone.utc),
    }
    document = products.find_one_and_update(
        {"_id": parse_object_id(product_id, "Product")},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    return serialize_product(document)


@router.put("/{product_id}", response_model=Product)
def update_product(product_id: str, payload: ProductUpdate) -> Product:
    changes = payload.model_dump(exclude_unset=True, mode="json", exclude={"image", "image_url"})
    if payload.image is not None:
        existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
        if existing is None:
            raise HTTPException(status_code=404, detail="Product not found")
        changes["image"] = apply_image_update(existing, payload.image)
    if payload.image_cdn is not None:
        changes["image_cdn"] = str(payload.image_cdn)
        changes["image_url"] = str(payload.image_cdn)
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
    existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")
    delete_product_image((existing.get("image") or {}).get("stored_image_id"))
    result = products.delete_one({"_id": existing["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
