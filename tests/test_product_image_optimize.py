"""Product upload optimize + R2 key helpers (no network)."""

from io import BytesIO
from unittest.mock import patch

from PIL import Image

from app.product_images import optimize_product_image
from app.r2_media import is_valid_object_key, new_product_image_key


def _jpeg_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(40, 120, 200))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_optimize_downscales_large_phone_photo():
    original = _jpeg_bytes(4000, 3000)
    body, content_type, ext = optimize_product_image(original, "image/jpeg")
    assert content_type == "image/jpeg"
    assert ext == ".jpg"
    assert len(body) < len(original)
    with Image.open(BytesIO(body)) as image:
        assert max(image.size) <= 1280


def test_product_image_keys_are_valid_for_r2():
    key = new_product_image_key(ext=".jpg")
    assert key.startswith("products/")
    assert is_valid_object_key(key)
    thumb = new_product_image_key(thumb=True, ext=".webp")
    assert "-thumb.webp" in thumb
    assert is_valid_object_key(thumb)


def test_save_upload_sets_cdn_when_r2_configured():
    from app.product_images import save_product_image

    original = _jpeg_bytes(1600, 1200)
    with (
        patch("app.product_images.r2_configured", return_value=True),
        patch("app.product_images.put_bytes") as put_mock,
        patch("app.product_images.public_url", return_value="https://media.example/products/abc.jpg"),
        patch("app.product_images.product_image_fs") as fs_mock,
    ):
        fs_mock.put.return_value = __import__("bson").ObjectId()
        stored = save_product_image(
            original,
            content_type="image/jpeg",
            filename="phone.jpg",
            source="upload",
            product_id="p1",
            store_id="s1",
        )
    assert stored.cdn == "https://media.example/products/abc.jpg"
    put_mock.assert_called_once()
