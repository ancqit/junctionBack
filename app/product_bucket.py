"""Shop product bucket — extra product packs under a shop plan (user JWT only).

Plans live on the shop (see PLAN_CATALOG):
  - free_trial: 40 products / 15 days
  - starter: 10 products / INR 999 / 1 year
  - growth: 80 products / INR 2999 / 1 year
  - premium: 150 products / INR 599 / 1 year

Extra capacity is sold in packs of 40 products for INR 999 each.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, model_validator

from .access_control import AuthenticatedUser, require_store_access, resolve_store_id
from .database import product_buckets, products
from .plan_service import PlanType, build_shop_plan_summary, get_shop_document, require_active_shop_plan
from .roles import UserRole, get_user_role

router = APIRouter(prefix="/product-bucket", tags=["product-bucket"])

BUCKET_PACK_SIZE = 40
BUCKET_PACK_PRICE_INR = 999
MAX_PACKS_PER_REQUEST = 50


class ProductBucketResponse(BaseModel):
    store_id: str
    plan_type: PlanType
    plan_name: str
    """Plan included product allowance."""
    plan_limit: int | None
    """Products currently listed for this shop."""
    products_count: int
    """Extra capacity from purchased packs (slots, not packs)."""
    extra_slots: int
    """Total capacity = plan_limit + extra_slots."""
    capacity: int | None
    """Slots still available before hitting capacity."""
    remaining: int | None
    """True when another product can be created under current capacity."""
    can_add_product: bool
    """True when plan allowance is fully used (packs are relevant)."""
    plan_allowance_consumed: bool
    pack_size: int = BUCKET_PACK_SIZE
    pack_price_inr: int = BUCKET_PACK_PRICE_INR


class AddBucketPacksRequest(BaseModel):
    store_id: str | None = Field(default=None, min_length=1, max_length=80)
    shop_id: str | None = Field(default=None, min_length=1, max_length=80, description="Alias of store_id")
    product_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Resolve the shop from this product",
    )
    packs: int = Field(
        ge=1,
        le=MAX_PACKS_PER_REQUEST,
        description=f"Number of packs to add. Each pack = {BUCKET_PACK_SIZE} products for INR {BUCKET_PACK_PRICE_INR}.",
    )

    @model_validator(mode="after")
    def require_shop_reference(self):
        if not ((self.store_id or "").strip() or (self.shop_id or "").strip() or (self.product_id or "").strip()):
            raise ValueError("store_id, shop_id, or product_id is required")
        return self


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


def apply_bucket_packs(store_id: str, packs: int) -> int:
    """Add pack capacity after payment succeeds. Returns slots added."""
    store_id = store_id.strip()
    slots_to_add = packs * BUCKET_PACK_SIZE
    _ensure_bucket_indexes()
    now = utc_now()
    product_buckets.update_one(
        {"store_id": store_id},
        {
            "$inc": {"extra_slots": slots_to_add, "packs_purchased": packs},
            "$set": {
                "updated_at": now,
                "last_pack_price_inr": BUCKET_PACK_PRICE_INR,
                "pack_size": BUCKET_PACK_SIZE,
            },
            "$setOnInsert": {"created_at": now, "store_id": store_id},
        },
        upsert=True,
    )
    return slots_to_add


def build_product_bucket(user: dict, store_id: str) -> ProductBucketResponse:
    store_id = store_id.strip()
    require_store_access(user, store_id)

    if get_user_role(user) == UserRole.admin:
        shop = get_shop_document(store_id)
        summary = build_shop_plan_summary(shop)
    else:
        _, summary = require_active_shop_plan(store_id)

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
        pack_size=BUCKET_PACK_SIZE,
        pack_price_inr=BUCKET_PACK_PRICE_INR,
    )


@router.get("", response_model=ProductBucketResponse)
def get_product_bucket(
    current_user: AuthenticatedUser,
    store_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    shop_id: Annotated[str | None, Query(min_length=1, max_length=80, description="Alias of store_id")] = None,
    product_id: Annotated[
        str | None,
        Query(min_length=1, max_length=80, description="Resolve the shop from this product"),
    ] = None,
) -> ProductBucketResponse:
    """JWT-only. Product count for a shop vs that shop's plan capacity + purchased packs.

    Identify the shop with store_id/shop_id, or pass product_id to look up that product's shop.
    """
    resolved = resolve_store_id(store_id=store_id, shop_id=shop_id, product_id=product_id)
    return build_product_bucket(current_user, resolved)


@router.post("/purchase", status_code=status.HTTP_201_CREATED)
def purchase_product_bucket_packs(
    payload: AddBucketPacksRequest,
    current_user: AuthenticatedUser,
):
    """
    Start a product-pack purchase for a shop (pending payment).
    Capacity is added only after POST /payments/{payment_id}/complete.
    """
    from .shop_payments import create_pack_purchase

    resolved = resolve_store_id(
        store_id=payload.store_id,
        shop_id=payload.shop_id,
        product_id=payload.product_id,
    )
    return create_pack_purchase(current_user, resolved, payload.packs)


@router.post("/slots", status_code=status.HTTP_201_CREATED)
def add_product_bucket_packs(
    payload: AddBucketPacksRequest,
    current_user: AuthenticatedUser,
):
    """
    Alias of POST /product-bucket/purchase.
    Creates a pending payment; packs apply after payment completion.
    Admins may pass ?fulfill=true to apply immediately without payment.
    """
    from .shop_payments import create_pack_purchase

    resolved = resolve_store_id(
        store_id=payload.store_id,
        shop_id=payload.shop_id,
        product_id=payload.product_id,
    )
    if get_user_role(current_user) == UserRole.admin:
        require_store_access(current_user, resolved)
        apply_bucket_packs(resolved, payload.packs)
        return build_product_bucket(current_user, resolved)

    return create_pack_purchase(current_user, resolved, payload.packs)
