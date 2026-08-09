from fastapi import APIRouter

from .plan_applications import (
    PlanApplication,
    PlanApplyPreview,
    PlanApplyRequest,
    apply_for_plan,
    get_my_plan_application,
    preview_plan_application,
)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

router.add_api_route(
    "/preview",
    preview_plan_application,
    methods=["GET"],
    response_model=PlanApplyPreview,
    summary="Preview waitlist application and plan-switch message",
)
router.add_api_route(
    "",
    apply_for_plan,
    methods=["POST"],
    response_model=PlanApplication,
    status_code=201,
    summary="Join the plan waitlist",
)
router.add_api_route(
    "/me",
    get_my_plan_application,
    methods=["GET"],
    response_model=PlanApplication | None,
    summary="Get your pending waitlist application",
)
