from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .login import get_current_user
from .plan_service import (
    PlanOption,
    PlanSummary,
    PlanType,
    build_plan_summary,
    cancel_plan_for_user,
    list_plan_options,
    select_plan_for_user,
)

router = APIRouter(prefix="/plans", tags=["plans"])


class SelectPlanRequest(BaseModel):
    plan_type: PlanType


@router.get("", response_model=list[PlanOption])
def list_plans() -> list[PlanOption]:
    trial_plan = PlanOption(
        type=PlanType.free_trial,
        name="Free Trial",
        price_inr=0,
        max_products=150,
        profile_only=False,
        description="Try all features free for 15 days",
        duration_days=15,
    )
    return [trial_plan, *list_plan_options()]


@router.get("/me", response_model=PlanSummary)
def get_my_plan(current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return build_plan_summary(current_user)


@router.post("/select", response_model=PlanSummary)
def select_plan(payload: SelectPlanRequest, current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return select_plan_for_user(current_user["_id"], payload.plan_type)


@router.post("/cancel", response_model=PlanSummary)
def cancel_plan(current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return cancel_plan_for_user(current_user["_id"])
