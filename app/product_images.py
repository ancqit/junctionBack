from __future__ import annotations

from io import BytesIO

import httpx
from bson import ObjectId
from fastapi import HTTPException, UploadFile, status
from gridfs import GridFS
from gridfs.errors import NoFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .database import database
from .r2_media import (
    new_product_image_key,
    public_url,
    put_bytes,
    r2_configured,
)

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
# Catalog display size — phone camera originals are huge; shrink before CDN/GridFS.
PRODUCT_IMAGE_MAX_EDGE = 1280
PRODUCT_IMAGE_JPEG_QUALITY = 82

product_image_fs = GridFS(database, collection="product_images")


class StoredProductImage:
    def __init__(
        self,
        file_id: ObjectId,
        content_type: str,
        filename: str,
        source: str,
        source_cdn: str | None,
        *,
        cdn: str | None = None,
        r2_object_key: str | None = None,
    ):
        self.file_id = file_id
        self.content_type = content_type
        self.filename = filename
        self.source = source
        self.source_cdn = source_cdn
        self.cdn = cdn
        self.r2_object_key = r2_object_key


def validate_image_upload(file: UploadFile, contents: bytes) -> str:
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPG, PNG, WEBP, and GIF images are supported",
        )
    if len(contents) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image must be 5 MB or smaller")
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded image is empty")
    return content_type


def optimize_product_image(contents: bytes, content_type: str) -> tuple[bytes, str, str]:
    """
    Downscale + re-encode for catalog use.
    Returns (bytes, content_type, filename_extension including dot).
    GIF stays as-is when Pillow cannot safely flatten animation.
    """
    if content_type == "image/gif":
        return contents, content_type, ".gif"

    try:
        with Image.open(BytesIO(contents)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")
            image.thumbnail((PRODUCT_IMAGE_MAX_EDGE, PRODUCT_IMAGE_MAX_EDGE), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=PRODUCT_IMAGE_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return buffer.getvalue(), "image/jpeg", ".jpg"
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid image",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not process uploaded image",
        ) from exc


def publish_product_image_cdn(contents: bytes, *, content_type: str, ext: str) -> tuple[str, str] | None:
    """Put optimized bytes on R2; return (public_cdn_url, object_key) or None if R2 is off."""
    if not r2_configured():
        return None
    key = new_product_image_key(ext=ext)
    put_bytes(key, contents, content_type=content_type)
    return public_url(key), key


def save_product_image(
    contents: bytes,
    *,
    content_type: str,
    filename: str,
    source: str,
    source_cdn: str | None = None,
    product_id: str | None = None,
    store_id: str | None = None,
    optimize: bool = True,
    publish_cdn: bool = True,
) -> StoredProductImage:
    stored_type = content_type
    stored_name = filename
    body = contents
    cdn: str | None = source_cdn
    r2_key: str | None = None

    if optimize and content_type != "image/gif":
        body, stored_type, ext = optimize_product_image(contents, content_type)
        stored_name = f"product-image{ext}"
    else:
        ext = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type, ".jpg")

    if publish_cdn and not (cdn and str(cdn).startswith("http")):
        published = publish_product_image_cdn(body, content_type=stored_type, ext=ext)
        if published:
            cdn, r2_key = published

    file_id = product_image_fs.put(
        body,
        content_type=stored_type,
        filename=stored_name,
        metadata={
            "source": source,
            "source_cdn": source_cdn,
            "cdn": cdn,
            "r2_object_key": r2_key,
            "product_id": product_id,
            "store_id": store_id,
        },
    )
    return StoredProductImage(
        file_id,
        stored_type,
        stored_name,
        source,
        source_cdn,
        cdn=cdn,
        r2_object_key=r2_key,
    )


async def fetch_image_from_cdn(cdn_url: str) -> tuple[bytes, str, str]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            response = await client.get(cdn_url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to download image from CDN") from exc

    if response.is_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to fetch image from the provided CDN URL")

    content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CDN image must be JPG, PNG, WEBP, or GIF",
        )

    contents = response.content
    if len(contents) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CDN image must be 5 MB or smaller")
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CDN image is empty")

    extension = ALLOWED_IMAGE_CONTENT_TYPES[content_type]
    filename = f"product-image{extension}"
    return contents, content_type, filename


def get_product_image(file_id: str) -> tuple[BytesIO, str, str]:
    try:
        object_id = ObjectId(file_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from exc

    try:
        grid_out = product_image_fs.get(object_id)
    except NoFile as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from exc

    return BytesIO(grid_out.read()), grid_out.content_type or "application/octet-stream", grid_out.filename or "product-image"


def get_product_image_with_meta(file_id: str) -> tuple[BytesIO, str, str, dict]:
    try:
        object_id = ObjectId(file_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from exc

    try:
        grid_out = product_image_fs.get(object_id)
    except NoFile as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from exc

    meta = dict(getattr(grid_out, "metadata", None) or {})
    return (
        BytesIO(grid_out.read()),
        grid_out.content_type or "application/octet-stream",
        grid_out.filename or "product-image",
        meta,
    )


def delete_product_image(file_id: str | None) -> None:
    if not file_id:
        return
    try:
        product_image_fs.delete(ObjectId(file_id))
    except Exception:
        return
