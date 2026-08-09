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
    list_all_plans,
    select_plan_for_user,
)

router = APIRouter(prefix="/plans", tags=["plans"])


class PlansListResponse(BaseModel):
    plans: list[PlanOption]


class SelectPlanRequest(BaseModel):
    plan_type: PlanType


@router.get("", response_model=PlansListResponse)
def list_plans() -> PlansListResponse:
    return PlansListResponse(plans=list_all_plans())


@router.get("/me", response_model=PlanSummary)
def get_my_plan(current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return build_plan_summary(current_user)


@router.post("/select", response_model=PlanSummary)
def select_plan(payload: SelectPlanRequest, current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return select_plan_for_user(current_user["_id"], payload.plan_type)


@router.post("/cancel", response_model=PlanSummary)
def cancel_plan(current_user: Annotated[dict, Depends(get_current_user)]) -> PlanSummary:
    return cancel_plan_for_user(current_user["_id"])
