import json
import os
import re

import httpx
from fastapi import HTTPException, status

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def require_gemini_configuration() -> str:
    api_key = GEMINI_API_KEY.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured",
        )
    return api_key


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


def generate_text(prompt: str) -> str:
    api_key = require_gemini_configuration()
    model = GEMINI_MODEL.strip() or "gemini-2.5-flash"

    try:
        response = httpx.post(
            GEMINI_GENERATE_URL.format(model=model),
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Gemini",
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid GEMINI_API_KEY",
        )
    if response.is_error:
        try:
            message = response.json().get("error", {}).get("message", "Gemini request failed")
        except ValueError:
            message = "Gemini request failed"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)

    return extract_gemini_text(response.json())


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
