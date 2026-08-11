import base64
import json
import os
import re

import httpx
from fastapi import HTTPException, status

# Single model for product descriptions (text) and product images (IMAGE modality).
# gemini-2.5-pro does not support image generation; use an image-capable model for both flows.
# Default: gemini-3-pro-image (higher quality text + images). Cheaper alternative: gemini-2.5-flash-image.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-image")
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TEXT_TIMEOUT_SECONDS = 60.0
IMAGE_TIMEOUT_SECONDS = 120.0


def get_gemini_model() -> str:
    return GEMINI_MODEL.strip() or "gemini-3-pro-image"


def require_gemini_configuration() -> str:
    api_key = GEMINI_API_KEY.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured",
        )
    return api_key


def _raise_for_gemini_error(response: httpx.Response, *, context: str) -> None:
    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Invalid GEMINI_API_KEY for {context}",
        )
    if response.is_error:
        try:
            message = response.json().get("error", {}).get("message", "Gemini request failed")
        except ValueError:
            message = "Gemini request failed"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)


def extract_gemini_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini did not return a response",
        )

    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts = [part.get("text", "").strip() for part in parts if part.get("text")]
    text = "\n".join(part for part in text_parts if part).strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini returned an empty response",
        )
    return text


def extract_gemini_image(payload: dict) -> tuple[bytes, str]:
    for candidate in payload.get("candidates") or []:
        for part in candidate.get("content", {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or not inline.get("data"):
                continue
            mime_type = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            return base64.b64decode(inline["data"]), mime_type

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Gemini did not return an image",
    )


def generate_text(prompt: str) -> str:
    api_key = require_gemini_configuration()
    model = get_gemini_model()

    try:
        response = httpx.post(
            GEMINI_GENERATE_URL.format(model=model),
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 4096,
                },
            },
            timeout=TEXT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Gemini",
        ) from exc

    _raise_for_gemini_error(response, context="text generation")

    return extract_gemini_text(response.json())


def generate_image_bytes(prompt: str) -> tuple[bytes, str]:
    api_key = require_gemini_configuration()
    model = get_gemini_model()

    try:
        response = httpx.post(
            GEMINI_GENERATE_URL.format(model=model),
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            },
            timeout=IMAGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Gemini image model",
        ) from exc

    _raise_for_gemini_error(response, context="image generation")

    return extract_gemini_image(response.json())


def parse_json_string_array(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini returned invalid JSON",
        ) from exc

    if not isinstance(data, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini JSON response must be an array",
        )

    values = [str(item).strip() for item in data if str(item).strip()]
    if not values:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini returned an empty JSON array",
        )
    return values
