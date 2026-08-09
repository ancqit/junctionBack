import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/terms-and-conditions", tags=["terms-and-conditions"])

DEFAULT_TERMS = {
    "title": "Terms and Conditions",
    "version": "1.0",
    "content": (
        "Welcome to Junction. By using our platform you agree to these terms.\n\n"
        "1. Account — You are responsible for activity on your account and keeping your phone number secure.\n"
        "2. Plans — Free trial, Starter, Growth, and Premium plans have the limits shown in the Plans section. "
        "Billing and renewals follow the plan you select.\n"
        "3. Shops & data — You own the shop and product data you enter. Do not upload unlawful or misleading content.\n"
        "4. Acceptable use — No abuse, spam, or attempts to disrupt the service.\n"
        "5. Changes — We may update these terms; continued use after changes means acceptance.\n"
        "6. Contact — Reach support through the app for billing or account issues."
    ),
}


class TermsAndConditions(BaseModel):
    title: str
    version: str
    content: str
    updated_at: datetime


def load_terms_and_conditions() -> TermsAndConditions:
    terms_json = os.getenv("TERMS_AND_CONDITIONS_JSON", "").strip()
    if terms_json:
        try:
            data = json.loads(terms_json)
            if isinstance(data, dict):
                return TermsAndConditions(
                    title=str(data.get("title", DEFAULT_TERMS["title"])),
                    version=str(data.get("version", DEFAULT_TERMS["version"])),
                    content=str(data.get("content", DEFAULT_TERMS["content"])),
                    updated_at=datetime.now(timezone.utc),
                )
        except json.JSONDecodeError:
            pass

    title = os.getenv("TERMS_AND_CONDITIONS_TITLE", DEFAULT_TERMS["title"]).strip() or DEFAULT_TERMS["title"]
    version = os.getenv("TERMS_AND_CONDITIONS_VERSION", DEFAULT_TERMS["version"]).strip() or DEFAULT_TERMS["version"]
    content = os.getenv("TERMS_AND_CONDITIONS_CONTENT", DEFAULT_TERMS["content"]).strip() or DEFAULT_TERMS["content"]

    return TermsAndConditions(
        title=title,
        version=version,
        content=content,
        updated_at=datetime.now(timezone.utc),
    )


@router.get("", response_model=TermsAndConditions)
def get_terms_and_conditions() -> TermsAndConditions:
    return load_terms_and_conditions()
