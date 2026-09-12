"""Public FX rates for plan/bucket display (base INR).

Uses the Frankfurter API (ECB reference rates) when reachable. Payments remain
INR; this is display-only conversion.

Always returns the full display set (INR, USD, EUR, GBP, AED, SGD). When the
provider is unreachable or omits a quote (AED is not on the classic ECB table),
we fill from static fallbacks / USD peg so the UI dropdown never collapses to
INR-only.
"""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/fx", tags=["fx"])

# Display currencies for plans / product bucket UI.
SUPPORTED_QUOTE_CURRENCIES = ("INR", "USD", "EUR", "GBP", "AED", "SGD")

# Classic Frankfurter (ECB) does not publish AED; request only supported quotes.
FRANKFURTER_QUOTES = ("USD", "EUR", "GBP", "SGD")
FRANKFURTER_URLS = (
    "https://api.frankfurter.app/latest",
    "https://api.frankfurter.dev/v1/latest",
)

# AED is USD-pegged (~3.6725 AED per 1 USD) for display estimates.
AED_PER_USD = 3.6725

# Approximate units of quote currency per 1 INR (display-only; refreshed from live when possible).
FALLBACK_RATES: dict[str, float] = {
    "INR": 1.0,
    "USD": 0.012,
    "EUR": 0.011,
    "GBP": 0.0095,
    "AED": 0.044,
    "SGD": 0.016,
}


class FxRatesResponse(BaseModel):
    base: str = "INR"
    as_of: str
    rates: dict[str, float] = Field(description="Units of quote currency per 1 INR")
    source: str = "frankfurter.app"


def _with_aed(rates: dict[str, float]) -> dict[str, float]:
    if "AED" in rates and rates["AED"] > 0:
        return rates
    usd = rates.get("USD")
    if usd and usd > 0:
        rates["AED"] = float(usd) * AED_PER_USD
    else:
        rates["AED"] = FALLBACK_RATES["AED"]
    return rates


def _complete_rates(partial: dict[str, float]) -> dict[str, float]:
    rates = {"INR": 1.0}
    for code in SUPPORTED_QUOTE_CURRENCIES:
        if code in ("INR", "AED"):
            continue
        value = partial.get(code)
        if value is not None and float(value) > 0:
            rates[code] = float(value)
        else:
            rates[code] = FALLBACK_RATES[code]
    # Prefer live AED when a provider returns it; otherwise derive from USD peg.
    aed = partial.get("AED")
    if aed is not None and float(aed) > 0:
        rates["AED"] = float(aed)
        return rates
    return _with_aed(rates)


def _fetch_frankfurter() -> tuple[dict[str, float], str, str] | None:
    """Return (rates, as_of, source) or None when every provider fails."""
    params = {"from": "INR", "to": ",".join(FRANKFURTER_QUOTES)}
    last_error: Exception | None = None
    for url in FRANKFURTER_URLS:
        try:
            response = httpx.get(url, params=params, timeout=10.0)
            if response.is_error:
                last_error = HTTPException(status_code=502, detail="FX provider returned an error")
                continue
            payload = response.json()
            remote_rates = payload.get("rates") or {}
            rates: dict[str, float] = {"INR": 1.0}
            for code in FRANKFURTER_QUOTES:
                value = remote_rates.get(code)
                if value is None:
                    continue
                rates[code] = float(value)
            if len(rates) <= 1:
                continue
            as_of = str(payload.get("date") or datetime.now(timezone.utc).date().isoformat())
            host = "frankfurter.app" if "frankfurter.app" in url else "frankfurter.dev"
            return rates, as_of, host
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            last_error = exc
            continue
    _ = last_error
    return None


@router.get("/rates", response_model=FxRatesResponse)
def get_fx_rates(
    base: str = Query(default="INR", min_length=3, max_length=3),
) -> FxRatesResponse:
    base_code = base.strip().upper()
    if base_code != "INR":
        raise HTTPException(status_code=400, detail="Only INR base is supported")

    fetched = _fetch_frankfurter()
    if fetched is None:
        rates = _complete_rates({})
        return FxRatesResponse(
            base="INR",
            as_of=datetime.now(timezone.utc).date().isoformat(),
            rates=rates,
            source="fallback",
        )

    live_rates, as_of, source = fetched
    rates = _complete_rates(live_rates)
    # Mark when AED (or any gap) was filled from peg/fallback while live quotes exist.
    if "AED" not in live_rates:
        source = f"{source}+aed-peg"
    return FxRatesResponse(base="INR", as_of=as_of, rates=rates, source=source)
