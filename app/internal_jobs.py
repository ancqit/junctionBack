"""Internal cron endpoints (Render / scheduler). Guarded by CRON_SECRET."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from .plan_enforcement import enforce_plans_once, ensure_viewer_day_log_indexes
from .plan_service import VIEWER_CLOSE_DAYS

router = APIRouter(prefix="/internal/jobs", tags=["internal-jobs"])


def _require_cron_secret(x_cron_secret: str | None) -> None:
    expected = (os.getenv("CRON_SECRET") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRON_SECRET is not configured",
        )
    if not x_cron_secret or x_cron_secret.strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret")


class EnforcePlansResponse(BaseModel):
    users_scanned: int
    shops_scanned: int
    downgraded: int
    closed: int
    log_rows: int
    plans_activated: int = 0
    trial_days: int | None = None
    ran_at: str
    catalog_trial_name: str | None = None
    viewer_close_days: int = VIEWER_CLOSE_DAYS


@router.post("/enforce-plans", response_model=EnforcePlansResponse)
def cron_enforce_plans(
    x_cron_secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
) -> EnforcePlansResponse:
    """Daily enforce: trial→selected plan after 15d, else expire→viewer→close; viewer-day logs."""
    _require_cron_secret(x_cron_secret)
    try:
        ensure_viewer_day_log_indexes()
    except Exception:
        pass
    result = enforce_plans_once()
    return EnforcePlansResponse(**result, viewer_close_days=VIEWER_CLOSE_DAYS)
