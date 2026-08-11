from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, status

from .gemini import generate_image_bytes
from .image_models import ImageResult
from .product_images import save_product_image

MAX_GENERATED_IMAGES_PER_REQUEST = 10
DEFAULT_IMAGE_WIDTH = 1024
DEFAULT_IMAGE_HEIGHT = 1024

PRODUCT_IMAGE_STYLES = [
    "front view on pure white background, studio ecommerce product photo",
    "45-degree angle product shot on white background, soft studio lighting",
    "side profile view, clean white background, commercial product photography",
    "close-up detail shot showing texture and materials",
    "lifestyle photo of the product in realistic everyday use",
    "top-down flat lay product photo on a minimal background",
    "back view of the product on white background",
    "retail packaging and box shot",
    "hands holding the product with natural lighting",
    "three-quarter hero shot like a Google Shopping listing",
]


def build_image_prompt(product_name: str, style: str) -> str:
    return (
        f"Create a photorealistic ecommerce product photograph for: {product_name}. "
        f"Style: {style}. "
        "The image must look like a real product photo from Google Shopping or Amazon. "
        "Use sharp focus, professional studio lighting, realistic materials, and accurate proportions. "
        "Do not include watermarks, text overlays, logos, or misspelled packaging text."
    )


def image_result_from_generated_image(
    *,
    keyword: str,
    style: str,
    base_url: str,
) -> ImageResult:
    contents, content_type = generate_image_bytes(build_image_prompt(keyword, style))
    extension = ".png" if "png" in content_type else ".jpg"
    stored = save_product_image(
        contents,
        content_type=content_type,
        filename=f"gemini-{keyword[:40].strip() or 'product'}{extension}",
        source="gemini",
        source_cdn=None,
    )
    image_url = f"{base_url.rstrip('/')}/products/images/{stored.file_id}"
    return ImageResult(
        id=str(stored.file_id),
        cdn_url=image_url,
        thumbnail_url=image_url,
        alt=f"{keyword} - {style}",
        width=DEFAULT_IMAGE_WIDTH,
        height=DEFAULT_IMAGE_HEIGHT,
        source="gemini",
        photographer=None,
        photographer_url=None,
    )


def generate_product_images(keyword: str, count: int, base_url: str) -> list[ImageResult]:
    cleaned_keyword = keyword.strip()
    if not cleaned_keyword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be blank")

    image_count = min(max(count, 1), MAX_GENERATED_IMAGES_PER_REQUEST)
    styles = [PRODUCT_IMAGE_STYLES[index % len(PRODUCT_IMAGE_STYLES)] for index in range(image_count)]

    images: list[ImageResult] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                image_result_from_generated_image,
                keyword=cleaned_keyword,
                style=style,
                base_url=base_url,
            )
            for style in styles
        ]
        for future in futures:
            try:
                images.append(future.result())
            except HTTPException as exc:
                errors.append(str(exc.detail))
            except Exception as exc:
                errors.append(str(exc))

    if not images:
        detail = errors[0] if errors else "Gemini could not generate images for this keyword"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    return images
