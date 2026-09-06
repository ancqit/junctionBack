"""Public FX rates for plan/bucket display (base INR).

Uses the Frankfurter API (ECB reference rates) as a free alternative to scraping
xe.com. Payments remain INR; this is display-only conversion.
"""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/fx", tags=["fx"])

# Display currencies for plans / product bucket UI.
SUPPORTED_QUOTE_CURRENCIES = ("INR", "USD", "EUR", "GBP", "AED", "SGD")
FRANKFURTER_URL = "https://api.frankfurter.app/latest"


class FxRatesResponse(BaseModel):
    base: str = "INR"
    as_of: str
    rates: dict[str, float] = Field(description="Units of quote currency per 1 INR")
    source: str = "frankfurter.app"


@router.get("/rates", response_model=FxRatesResponse)
def get_fx_rates(
    base: str = Query(default="INR", min_length=3, max_length=3),
) -> FxRatesResponse:
    base_code = base.strip().upper()
    if base_code != "INR":
        raise HTTPException(status_code=400, detail="Only INR base is supported")

    quotes = [code for code in SUPPORTED_QUOTE_CURRENCIES if code != "INR"]
    try:
        response = httpx.get(
            FRANKFURTER_URL,
            params={"from": "INR", "to": ",".join(quotes)},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach FX provider") from exc

    if response.is_error:
        raise HTTPException(status_code=502, detail="FX provider returned an error")

    payload = response.json()
    remote_rates = payload.get("rates") or {}
    rates: dict[str, float] = {"INR": 1.0}
    for code in quotes:
        value = remote_rates.get(code)
        if value is None:
            continue
        rates[code] = float(value)

    as_of = str(payload.get("date") or datetime.now(timezone.utc).date().isoformat())
    return FxRatesResponse(base="INR", as_of=as_of, rates=rates, source="frankfurter.app")
