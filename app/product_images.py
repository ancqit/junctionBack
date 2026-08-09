from io import BytesIO

import httpx
from bson import ObjectId
from fastapi import HTTPException, UploadFile, status
from gridfs import GridFS
from gridfs.errors import NoFile

from .database import database

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

product_image_fs = GridFS(database, collection="product_images")


class StoredProductImage:
    def __init__(self, file_id: ObjectId, content_type: str, filename: str, source: str, source_cdn: str | None):
        self.file_id = file_id
        self.content_type = content_type
        self.filename = filename
        self.source = source
        self.source_cdn = source_cdn


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


def save_product_image(
    contents: bytes,
    *,
    content_type: str,
    filename: str,
    source: str,
    source_cdn: str | None = None,
    product_id: str | None = None,
    store_id: str | None = None,
) -> StoredProductImage:
    file_id = product_image_fs.put(
        contents,
        content_type=content_type,
        filename=filename,
        metadata={
            "source": source,
            "source_cdn": source_cdn,
            "product_id": product_id,
            "store_id": store_id,
        },
    )
    return StoredProductImage(file_id, content_type, filename, source, source_cdn)


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


def delete_product_image(file_id: str | None) -> None:
    if not file_id:
        return
    try:
        product_image_fs.delete(ObjectId(file_id))
    except Exception:
        return
