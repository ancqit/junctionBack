import os

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/descriptions", tags=["descriptions"])

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DESCRIPTION_PROMPT = (
    "You are writing product copy for an online shop catalog. "
    "Turn the following short product summary into a clear, detailed product description. "
    "Use complete sentences, highlight key features and benefits, and keep a professional tone. "
    "Do not invent specifications that are not implied by the summary. "
    "Return only the description text with no title, labels, or markdown.\n\n"
    "Summary:\n{text}"
)


class DescriptionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class DescriptionResponse(BaseModel):
    description: str


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
            detail="Gemini did not return a description",
        )

    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts = [part.get("text", "").strip() for part in parts if part.get("text")]
    description = "\n".join(part for part in text_parts if part).strip()
    if not description:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini returned an empty description",
        )
    return description


def generate_description(text: str) -> str:
    api_key = require_gemini_configuration()
    model = GEMINI_MODEL.strip() or "gemini-2.0-flash"
    prompt = DESCRIPTION_PROMPT.format(text=text)

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


@router.post("/generate", response_model=DescriptionResponse, status_code=status.HTTP_200_OK)
def generate_product_description(payload: DescriptionRequest) -> DescriptionResponse:
    return DescriptionResponse(description=generate_description(payload.text))
