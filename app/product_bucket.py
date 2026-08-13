"""Shop product bucket — plan-aligned capacity under user JWT (not guest session).

Plans (junctionBack PLAN_CATALOG):
  - starter: 10 products
  - growth: 100 products
  - premium: unlimited (null)
  - free_trial: 150 products

The bucket reports how many products a shop currently has vs plan capacity.
When the shop has consumed the plan allowance, owners can POST extra slots
so they can keep adding products without upgrading the plan immediately.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .access_control import AuthenticatedUser, require_store_access
from .database import product_buckets, products
from .plan_service import PlanType, build_plan_summary, require_active_plan
from .roles import UserRole, get_user_role

router = APIRouter(prefix="/product-bucket", tags=["product-bucket"])

MAX_SLOTS_PER_REQUEST = 500


class ProductBucketResponse(BaseModel):
    store_id: str
    plan_type: PlanType
    plan_name: str
    """Plan included product allowance (null = unlimited, e.g. Premium)."""
    plan_limit: int | None
    """Products currently listed for this shop."""
    products_count: int
    """Extra capacity purchased/added beyond the plan limit."""
    extra_slots: int
    """Total capacity = plan_limit + extra_slots (null = unlimited)."""
    capacity: int | None
    """Slots still available before hitting capacity (null = unlimited)."""
    remaining: int | None
    """True when another product can be created under current capacity."""
    can_add_product: bool
    """True when plan allowance is fully used (extra slots are relevant)."""
    plan_allowance_consumed: bool


class AddBucketSlotsRequest(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=MAX_SLOTS_PER_REQUEST)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_bucket_indexes() -> None:
    product_buckets.create_index("store_id", unique=True)


def get_extra_slots(store_id: str) -> int:
    document = product_buckets.find_one({"store_id": store_id.strip()})
    if document is None:
        return 0
    try:
        return max(0, int(document.get("extra_slots") or 0))
    except (TypeError, ValueError):
        return 0


def build_product_bucket(user: dict, store_id: str) -> ProductBucketResponse:
    store_id = store_id.strip()
    require_store_access(user, store_id)

    if get_user_role(user) == UserRole.admin:
        summary = build_plan_summary(user)
    else:
        summary = require_active_plan(user)

    products_count = products.count_documents({"store_id": store_id})
    extra_slots = get_extra_slots(store_id)
    plan_limit = summary.max_products

    if plan_limit is None:
        capacity: int | None = None
        remaining: int | None = None
        can_add = True
        plan_allowance_consumed = False
    else:
        capacity = plan_limit + extra_slots
        remaining = max(0, capacity - products_count)
        can_add = products_count < capacity and not summary.profile_only
        plan_allowance_consumed = products_count >= plan_limit

    return ProductBucketResponse(
        store_id=store_id,
        plan_type=summary.type,
        plan_name=summary.name,
        plan_limit=plan_limit,
        products_count=products_count,
        extra_slots=extra_slots,
        capacity=capacity,
        remaining=remaining,
        can_add_product=can_add,
        plan_allowance_consumed=plan_allowance_consumed,
    )


@router.get("", response_model=ProductBucketResponse)
def get_product_bucket(
    current_user: AuthenticatedUser,
    store_id: Annotated[str, Query(min_length=1, max_length=80)],
) -> ProductBucketResponse:
    """
    JWT-only. Returns product count for a shop vs plan-aligned bucket capacity.
    Starter / Growth / Premium (and free trial) limits come from the owner's plan.
    """
    return build_product_bucket(current_user, store_id)


@router.post("/slots", response_model=ProductBucketResponse, status_code=status.HTTP_200_OK)
def add_product_bucket_slots(
    payload: AddBucketSlotsRequest,
    current_user: AuthenticatedUser,
) -> ProductBucketResponse:
    """
    JWT-only. Add extra product capacity to the shop bucket after the plan
    allowance has been consumed (Starter 10 / Growth 100 / …).
    Premium (unlimited) does not need extra slots.
    """
    store_id = payload.store_id.strip()
    require_store_access(current_user, store_id)
    summary = require_active_plan(current_user)

    if summary.profile_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan does not include products. Upgrade to add capacity.",
        )

    if summary.max_products is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your plan already allows unlimited products; extra bucket slots are not needed.",
        )

    products_count = products.count_documents({"store_id": store_id})
    if products_count < summary.max_products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"You still have plan allowance remaining "
                f"({products_count}/{summary.max_products}). "
                "Extra bucket slots can be added after the plan products are used."
            ),
        )

    _ensure_bucket_indexes()
    now = utc_now()
    product_buckets.update_one(
        {"store_id": store_id},
        {
            "$inc": {"extra_slots": payload.quantity},
            "$set": {"updated_at": now},
            "$setOnInsert": {"created_at": now, "store_id": store_id},
        },
        upsert=True,
    )
    return build_product_bucket(current_user, store_id)
