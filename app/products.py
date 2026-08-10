from datetime import datetime, timezone
from enum import Enum
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import products
from .login import get_current_user
from .plan_service import ensure_can_add_product
from .product_images import (
    delete_product_image,
    fetch_image_from_cdn,
    get_product_image,
    save_product_image,
    validate_image_upload,
)
from .queries import ProductImageSuggestResponse, collect_suggested_images, request_base_url
from .utils import parse_object_id

router = APIRouter(prefix="/products", tags=["products"])

MAX_PRODUCT_IMAGES = 5
INTERNAL_IMAGE_CDN_PATTERN = re.compile(r"/products/images/([a-fA-F0-9]{24})(?:\?.*)?$")


class ProductStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    discontinued = "discontinued"


class ProductImageSource(str, Enum):
    cdn = "cdn"
    query = "query"
    upload = "upload"
    gemini = "gemini"


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


class ProductImagesSuggestRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)

    @field_validator("product_name")
    @classmethod
    def product_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be blank")
        return value


class ProductImagesAttachRequest(BaseModel):
    cdns: list[HttpUrl] = Field(min_length=1, max_length=MAX_PRODUCT_IMAGES)

    @field_validator("cdns")
    @classmethod
    def cdns_must_be_unique(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        normalized = [str(cdn) for cdn in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("cdns must not contain duplicates")
        return value


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
    images: list[ProductImage] = Field(default_factory=list, max_length=MAX_PRODUCT_IMAGES)
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
    images: list[ProductImage] | None = Field(default=None, max_length=MAX_PRODUCT_IMAGES)
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
    images: list[ProductImage] = Field(default_factory=list, max_length=MAX_PRODUCT_IMAGES)
    barcode: str | None = None
    tax_rate: float | None = None
    low_stock_threshold: int | None = None
    created_at: datetime
    updated_at: datetime


def product_images_from_document(document: dict) -> list[ProductImage]:
    images_data = document.get("images") or []
    if images_data:
        return [ProductImage(**image_data) for image_data in images_data]

    image_data = document.get("image")
    if image_data:
        return [ProductImage(**image_data)]
    return []


def hero_image_fields(images: list[ProductImage]) -> dict:
    if not images:
        return {"image": None, "image_cdn": None, "image_url": None}

    hero = images[0]
    hero_cdn = str(hero.cdn) if hero.cdn else None
    return {
        "image": hero.model_dump(mode="json"),
        "image_cdn": hero_cdn,
        "image_url": hero_cdn,
    }


def images_payload(images: list[ProductImage]) -> list[dict]:
    return [image.model_dump(mode="json") for image in images[:MAX_PRODUCT_IMAGES]]


def delete_stored_images(images: list[ProductImage]) -> None:
    for image in images:
        delete_product_image(image.stored_image_id)


def serialize_product(document: dict) -> Product:
    gallery = product_images_from_document(document)
    hero = gallery[0] if gallery else None
    image_cdn = document.get("image_cdn") or (hero.cdn if hero else None) or document.get("image_url")
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
        image=hero,
        image_url=image_cdn,
        images=gallery,
        barcode=document.get("barcode"),
        tax_rate=document.get("tax_rate"),
        low_stock_threshold=document.get("low_stock_threshold"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def product_document_from_payload(payload: ProductCreate) -> dict:
    document = payload.model_dump(mode="json", exclude={"image", "image_url", "images"})
    gallery = list(payload.images)
    if not gallery and payload.image is not None:
        gallery = [payload.image]
    elif not gallery and payload.image_cdn is not None:
        gallery = [ProductImage(source=ProductImageSource.cdn, cdn=payload.image_cdn)]

    if len(gallery) > MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_PRODUCT_IMAGES} images",
        )

    document["images"] = images_payload(gallery)
    document.update(hero_image_fields(gallery))
    return document


async def product_image_from_cdn_url(
    *,
    cdn_url: str,
    product_id: str,
    store_id: str | None,
) -> ProductImage:
    match = INTERNAL_IMAGE_CDN_PATTERN.search(cdn_url)
    if match:
        return ProductImage(
            source=ProductImageSource.gemini,
            cdn=cdn_url,
            stored_image_id=match.group(1),
        )

    return await store_product_image_from_cdn(
        cdn_url=cdn_url,
        product_id=product_id,
        store_id=store_id,
    )


async def store_product_image_from_cdn(
    *,
    cdn_url: str,
    product_id: str,
    store_id: str | None,
) -> ProductImage:
    contents, content_type, filename = await fetch_image_from_cdn(cdn_url)
    stored = save_product_image(
        contents,
        content_type=content_type,
        filename=filename,
        source=ProductImageSource.query.value,
        source_cdn=cdn_url,
        product_id=product_id,
        store_id=store_id,
    )
    return ProductImage(
        source=ProductImageSource.query,
        cdn=cdn_url,
        stored_image_id=str(stored.file_id),
        content_type=stored.content_type,
        filename=stored.filename,
    )


@router.post("/images/suggest", response_model=ProductImageSuggestResponse, status_code=status.HTTP_200_OK)
def suggest_product_images(payload: ProductImagesSuggestRequest, request: Request) -> ProductImageSuggestResponse:
    return collect_suggested_images(
        payload.product_name,
        count=10,
        base_url=request_base_url(request),
    )


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
def create_product(payload: ProductCreate, current_user: Annotated[dict, Depends(get_current_user)]) -> Product:
    ensure_can_add_product(current_user, payload.store_id)
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

    gallery = product_images_from_document(product)
    if len(gallery) >= MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_PRODUCT_IMAGES} images",
        )

    image = await product_image_from_cdn_url(
        cdn_url=str(payload.cdn),
        product_id=product_id,
        store_id=product.get("store_id"),
    )
    if gallery:
        return update_product_images(product_id, gallery + [image])
    return update_product_images(product_id, [image])


@router.post("/{product_id}/images", response_model=Product)
async def attach_product_images_from_cdns(product_id: str, payload: ProductImagesAttachRequest) -> Product:
    if len(payload.cdns) > MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Select at most {MAX_PRODUCT_IMAGES} images per product",
        )

    product = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    stored_images: list[ProductImage] = []
    for cdn in payload.cdns:
        stored_images.append(
            await product_image_from_cdn_url(
                cdn_url=str(cdn),
                product_id=product_id,
                store_id=product.get("store_id"),
            )
        )

    return update_product_images(product_id, stored_images, replace_existing=True)


@router.post("/{product_id}/image/upload", response_model=Product)
async def upload_product_image(product_id: str, file: UploadFile = File(...)) -> Product:
    product = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    gallery = product_images_from_document(product)
    if len(gallery) >= MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_PRODUCT_IMAGES} images",
        )

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
    if gallery:
        return update_product_images(product_id, gallery + [image])
    return update_product_images(product_id, [image])


def update_product_images(
    product_id: str,
    images: list[ProductImage],
    *,
    replace_existing: bool = False,
) -> Product:
    if len(images) > MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A product can have at most {MAX_PRODUCT_IMAGES} images",
        )

    existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    if replace_existing:
        delete_stored_images(product_images_from_document(existing))

    changes = {
        "images": images_payload(images),
        "updated_at": datetime.now(timezone.utc),
    }
    changes.update(hero_image_fields(images))
    document = products.find_one_and_update(
        {"_id": parse_object_id(product_id, "Product")},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    return serialize_product(document)


def update_product_image(product_id: str, image: ProductImage, image_cdn: str | None = None) -> Product:
    existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if existing is None:
        raise HTTPException(status_code=404, detail="Product not found")

    gallery = product_images_from_document(existing)
    if gallery:
        previous_image = gallery[0]
        if previous_image.stored_image_id and previous_image.stored_image_id != image.stored_image_id:
            delete_product_image(previous_image.stored_image_id)
        gallery[0] = image
    else:
        gallery = [image]

    if image_cdn and image.cdn is None:
        image = image.model_copy(update={"cdn": image_cdn})

    if gallery:
        gallery[0] = image

    return update_product_images(product_id, gallery[:MAX_PRODUCT_IMAGES], replace_existing=True)


@router.put("/{product_id}", response_model=Product)
def update_product(product_id: str, payload: ProductUpdate) -> Product:
    changes = payload.model_dump(exclude_unset=True, mode="json", exclude={"image", "image_url", "images"})
    if payload.image is not None:
        existing = products.find_one({"_id": parse_object_id(product_id, "Product")})
        if existing is None:
            raise HTTPException(status_code=404, detail="Product not found")
        gallery = product_images_from_document(existing)
        if gallery:
            gallery[0] = payload.image
        else:
            gallery = [payload.image]
        changes["images"] = images_payload(gallery[:MAX_PRODUCT_IMAGES])
        changes.update(hero_image_fields(gallery[:MAX_PRODUCT_IMAGES]))
    if payload.images is not None:
        if len(payload.images) > MAX_PRODUCT_IMAGES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A product can have at most {MAX_PRODUCT_IMAGES} images",
            )
        changes["images"] = images_payload(payload.images)
        changes.update(hero_image_fields(payload.images))
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
    delete_stored_images(product_images_from_document(existing))
    result = products.delete_one({"_id": existing["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
