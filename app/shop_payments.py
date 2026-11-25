"""Shop plan and product-pack purchases — activate only after payment completes."""

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .access_control import require_store_access
from .database import shop_payments, users
from .login import get_current_user
from .plan_service import (
    PLAN_CATALOG,
    PlanSummary,
    PlanType,
    build_shop_plan_summary,
    get_shop_document,
    require_active_shop_plan,
    select_plan_for_shop,
)
from .product_bucket import (
    BUCKET_PACK_PRICE_INR,
    BUCKET_PACK_SIZE,
    MAX_PACKS_PER_REQUEST,
    ProductBucketResponse,
    apply_bucket_packs,
    build_product_bucket,
)
from .razorpay_client import (
    RAZORPAY_KEY_ID,
    create_order,
    razorpay_configured,
    require_razorpay,
    verify_checkout_signature,
    verify_webhook_signature,
    webhook_configured,
)
from .roles import UserRole
from .utils import parse_object_id

router = APIRouter(prefix="/payments", tags=["payments"])

# Junction shop plan/pack checkout — GST on taxable base (Razorpay charges total).
GST_RATE = 0.18


class PaymentKind(str, Enum):
    plan = "plan"
    product_pack = "product_pack"
    plan_and_pack = "plan_and_pack"


class ShopPaymentStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    cancelled = "cancelled"


class PaymentMethod(str, Enum):
    cash = "cash"
    card = "card"
    upi = "upi"
    bank_transfer = "bank_transfer"
    other = "other"


class ShopPayment(BaseModel):
    id: str
    store_id: str
    owner_user_id: str
    kind: PaymentKind
    status: ShopPaymentStatus
    amount_inr: int
    currency: str = "INR"
    base_amount_inr: int | None = None
    gst_rate: float | None = None
    gst_amount_inr: int | None = None
    plan_type: PlanType | None = None
    packs: int | None = None
    slots: int | None = None
    description: str
    payment_method: PaymentMethod | None = None
    payment_reference: str | None = None
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    fulfilled_at: datetime | None = None


class PlanPurchaseRequest(BaseModel):
    plan_type: PlanType


class PackPurchaseRequest(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    packs: int = Field(
        ge=1,
        le=MAX_PACKS_PER_REQUEST,
        description=f"Number of packs. Each pack = {BUCKET_PACK_SIZE} products for INR {BUCKET_PACK_PRICE_INR}.",
    )


class PreparePurchaseRequest(BaseModel):
    """One Razorpay bill: plan-only, pack-only, or plan + packs together."""

    store_id: str = Field(min_length=1, max_length=80)
    plan_type: PlanType | None = None
    packs: int = Field(
        default=0,
        ge=0,
        le=MAX_PACKS_PER_REQUEST,
        description=f"Optional packs. Each pack = {BUCKET_PACK_SIZE} products for INR {BUCKET_PACK_PRICE_INR}.",
    )


class CompletePaymentRequest(BaseModel):
    payment_method: PaymentMethod | None = None
    payment_reference: str | None = Field(default=None, max_length=200)


class FailPaymentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RazorpayCheckoutConfirmRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=80)
    razorpay_payment_id: str = Field(min_length=1, max_length=80)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class RazorpayOrderResponse(BaseModel):
    payment_id: str
    key_id: str
    order_id: str
    amount_paise: int
    currency: str = "INR"
    name: str = "Junction"
    description: str
    prefill: dict[str, str] = Field(default_factory=dict)


class ShopPaymentCompleteResponse(BaseModel):
    payment: ShopPayment
    plan: PlanSummary | None = None
    product_bucket: ProductBucketResponse | None = None
    message: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def apply_gst(base_amount_inr: int) -> dict[str, float | int]:
    """Return taxable base, GST (18%), and Razorpay total (base + GST)."""
    base = max(0, int(base_amount_inr))
    gst = int(round(base * GST_RATE))
    return {
        "base_amount_inr": base,
        "gst_rate": GST_RATE,
        "gst_amount_inr": gst,
        "amount_inr": base + gst,
    }


def _ensure_payment_indexes() -> None:
    shop_payments.create_index("store_id")
    shop_payments.create_index("owner_user_id")
    shop_payments.create_index([("store_id", 1), ("status", 1)])
    shop_payments.create_index("razorpay_order_id", sparse=True)


def serialize_payment(document: dict) -> ShopPayment:
    amount = int(document["amount_inr"])
    base = document.get("base_amount_inr")
    gst_amount = document.get("gst_amount_inr")
    gst_rate = document.get("gst_rate")
    return ShopPayment(
        id=str(document["_id"]),
        store_id=document["store_id"],
        owner_user_id=document["owner_user_id"],
        kind=PaymentKind(document["kind"]),
        status=ShopPaymentStatus(document["status"]),
        amount_inr=amount,
        currency=document.get("currency", "INR"),
        base_amount_inr=int(base) if base is not None else amount,
        gst_rate=float(gst_rate) if gst_rate is not None else None,
        gst_amount_inr=int(gst_amount) if gst_amount is not None else None,
        plan_type=PlanType(document["plan_type"]) if document.get("plan_type") else None,
        packs=document.get("packs"),
        slots=document.get("slots"),
        description=document["description"],
        payment_method=PaymentMethod(document["payment_method"]) if document.get("payment_method") else None,
        payment_reference=document.get("payment_reference"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        paid_at=document.get("paid_at"),
        fulfilled_at=document.get("fulfilled_at"),
    )


def _require_shop_access(user: dict, store_id: str) -> dict:
    shop = get_shop_document(store_id)
    require_store_access(user, store_id)
    return shop


def create_plan_purchase(user: dict, store_id: str, plan_type: PlanType) -> ShopPayment:
    return create_prepared_purchase(
        user,
        PreparePurchaseRequest(store_id=store_id, plan_type=plan_type, packs=0),
    )


def create_pack_purchase(user: dict, store_id: str, packs: int) -> ShopPayment:
    return create_prepared_purchase(
        user,
        PreparePurchaseRequest(store_id=store_id, plan_type=None, packs=packs),
    )


def _zero_priced() -> dict[str, float | int]:
    return {
        "base_amount_inr": 0,
        "gst_rate": GST_RATE,
        "gst_amount_inr": 0,
        "amount_inr": 0,
    }


def create_prepared_purchase(user: dict, payload: PreparePurchaseRequest) -> ShopPayment:
    """
    Build one pending payment for Razorpay:
    plan-only, pack-only, or plan + packs on a single bill (per-line 18% GST).
    """
    store_id = payload.store_id.strip()
    shop = _require_shop_access(user, store_id)

    plan_type = payload.plan_type
    packs = int(payload.packs or 0)
    if plan_type == PlanType.free_trial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Free trial starts automatically when the shop is created; no payment needed.",
        )

    has_plan = plan_type is not None
    has_packs = packs > 0
    if not has_plan and not has_packs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a plan and/or at least one product pack.",
        )

    plan_base = 0
    plan_name = ""
    plan_max: int | None = None
    if has_plan:
        assert plan_type is not None
        details = PLAN_CATALOG[plan_type.value]
        plan_base = int(details["price_inr"])
        plan_name = str(details["name"])
        plan_max = details.get("max_products")
        if plan_base <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This plan does not require payment. Contact support if activation is stuck.",
            )

    if has_packs:
        # Packs need a product-capable plan after fulfillment (selected now or already active).
        if has_plan:
            if plan_max == 0:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This shop plan does not include products. Choose a product plan first.",
                )
            if plan_max is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This shop plan already allows unlimited products; packs are not needed.",
                )
        else:
            _, summary = require_active_shop_plan(store_id)
            if summary.profile_only:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This shop plan does not include products. Purchase a product plan first.",
                )
            if summary.max_products is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This shop plan already allows unlimited products; packs are not needed.",
                )

    pack_base = packs * BUCKET_PACK_PRICE_INR if has_packs else 0
    slots = packs * BUCKET_PACK_SIZE if has_packs else 0
    plan_priced = apply_gst(plan_base) if plan_base else _zero_priced()
    pack_priced = apply_gst(pack_base) if pack_base else _zero_priced()
    base_amount = int(plan_priced["base_amount_inr"]) + int(pack_priced["base_amount_inr"])
    gst_amount = int(plan_priced["gst_amount_inr"]) + int(pack_priced["gst_amount_inr"])
    amount_inr = int(plan_priced["amount_inr"]) + int(pack_priced["amount_inr"])

    if has_plan and has_packs:
        kind = PaymentKind.plan_and_pack
        description = (
            f"{plan_name} plan + {packs} product pack(s) (+{slots} products) + 18% GST"
        )
    elif has_plan:
        kind = PaymentKind.plan
        description = (
            f"{plan_name} plan for shop ({plan_max} products / 1 year) + 18% GST"
        )
    else:
        kind = PaymentKind.product_pack
        description = f"{packs} product pack(s) (+{slots} products) for shop + 18% GST"

    now = utc_now()
    _ensure_payment_indexes()
    # One active unpaid checkout per shop — cancel prior pending noise.
    shop_payments.update_many(
        {
            "store_id": store_id,
            "status": ShopPaymentStatus.pending.value,
            "dismissed_at": {"$exists": False},
        },
        {
            "$set": {
                "status": ShopPaymentStatus.cancelled.value,
                "updated_at": now,
                "dismissed_at": now,
            }
        },
    )

    document = {
        "store_id": store_id,
        "owner_user_id": str(shop.get("owner_user_id") or user["_id"]),
        "kind": kind.value,
        "status": ShopPaymentStatus.pending.value,
        "amount_inr": amount_inr,
        "base_amount_inr": base_amount,
        "gst_rate": GST_RATE,
        "gst_amount_inr": gst_amount,
        "plan_base_amount_inr": int(plan_priced["base_amount_inr"]) or None,
        "pack_base_amount_inr": int(pack_priced["base_amount_inr"]) or None,
        "currency": "INR",
        "plan_type": plan_type.value if has_plan and plan_type is not None else None,
        "packs": packs if has_packs else None,
        "slots": slots if has_packs else None,
        "description": description,
        "payment_method": None,
        "payment_reference": None,
        "created_at": now,
        "updated_at": now,
        "paid_at": None,
        "fulfilled_at": None,
    }
    result = shop_payments.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_payment(document)


def _get_payment_for_user(user: dict, payment_id: str) -> dict:
    document = shop_payments.find_one({"_id": parse_object_id(payment_id, "Payment")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    require_store_access(user, document["store_id"])
    return document


def fulfill_paid_payment(user: dict, document: dict) -> ShopPaymentCompleteResponse:
    """Apply plan and/or packs after payment is marked paid. Idempotent if already fulfilled."""
    store_id = document["store_id"]
    kind = PaymentKind(document["kind"])
    now = utc_now()

    if document.get("fulfilled_at") is not None and document.get("status") == ShopPaymentStatus.paid.value:
        plan = build_shop_plan_summary(get_shop_document(store_id))
        bucket = None
        if kind in {PaymentKind.product_pack, PaymentKind.plan_and_pack}:
            bucket = build_product_bucket(user, store_id)
        return ShopPaymentCompleteResponse(
            payment=serialize_payment(document),
            plan=plan,
            product_bucket=bucket,
            message="Payment already completed; plan/products capacity is active.",
        )

    plan = None
    bucket = None
    messages: list[str] = []

    if kind in {PaymentKind.plan, PaymentKind.plan_and_pack}:
        plan_type = PlanType(document["plan_type"])
        plan = select_plan_for_shop(store_id, plan_type)
        messages.append(
            f"{plan.name} is now active for this shop. "
            f"You can add up to {plan.max_products} products."
        )

    if kind in {PaymentKind.product_pack, PaymentKind.plan_and_pack}:
        packs = int(document["packs"])
        apply_bucket_packs(store_id, packs)
        bucket = build_product_bucket(user, store_id)
        messages.append(
            f"Added {packs * BUCKET_PACK_SIZE} product slots. You can add more products now."
        )

    if plan is None:
        plan = build_shop_plan_summary(get_shop_document(store_id))

    shop_payments.update_one(
        {"_id": document["_id"]},
        {"$set": {"fulfilled_at": now, "updated_at": now}},
    )
    document["fulfilled_at"] = now
    document["updated_at"] = now
    return ShopPaymentCompleteResponse(
        payment=serialize_payment(document),
        plan=plan,
        product_bucket=bucket,
        message=" ".join(messages) or "Payment completed.",
    )


def complete_payment(user: dict, payment_id: str, payload: CompletePaymentRequest) -> ShopPaymentCompleteResponse:
    document = _get_payment_for_user(user, payment_id)
    status_value = ShopPaymentStatus(document["status"])

    if status_value == ShopPaymentStatus.failed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This payment has failed")
    if status_value == ShopPaymentStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This payment was cancelled")

    now = utc_now()
    if status_value == ShopPaymentStatus.pending:
        updates: dict = {
            "status": ShopPaymentStatus.paid.value,
            "paid_at": now,
            "updated_at": now,
        }
        if payload.payment_method is not None:
            updates["payment_method"] = payload.payment_method.value
        if payload.payment_reference is not None:
            ref = payload.payment_reference.strip()
            updates["payment_reference"] = ref or None
        shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
        document.update(updates)

    return fulfill_paid_payment(user, document)


def _owner_user_for_payment(document: dict) -> dict:
    owner_id = str(document.get("owner_user_id") or "").strip()
    if owner_id and ObjectId.is_valid(owner_id):
        owner = users.find_one({"_id": ObjectId(owner_id)})
        if owner is not None:
            return owner
    return {
        "_id": ObjectId(owner_id) if owner_id and ObjectId.is_valid(owner_id) else ObjectId(),
        "role": UserRole.owner.value,
    }


def _mark_paid_from_razorpay(
    document: dict,
    *,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    source: str,
) -> dict:
    """Idempotently mark pending payment paid using Razorpay ids."""
    status_value = ShopPaymentStatus(document["status"])
    if status_value == ShopPaymentStatus.paid:
        return document
    if status_value in {ShopPaymentStatus.failed, ShopPaymentStatus.cancelled}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete a payment in status {document['status']}",
        )

    stored_order = str(document.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != razorpay_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Razorpay order id does not match this payment",
        )

    now = utc_now()
    updates = {
        "status": ShopPaymentStatus.paid.value,
        "paid_at": now,
        "updated_at": now,
        "payment_method": PaymentMethod.other.value,
        "payment_reference": razorpay_payment_id,
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_source": source,
    }
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return document


def _mark_failed_from_razorpay(document: dict, *, reason: str | None, source: str) -> dict:
    status_value = ShopPaymentStatus(document["status"])
    if status_value != ShopPaymentStatus.pending:
        return document
    now = utc_now()
    updates = {
        "status": ShopPaymentStatus.failed.value,
        "updated_at": now,
        "failure_reason": (reason or "").strip() or None,
        "razorpay_source": source,
    }
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return document


def confirm_razorpay_checkout(
    user: dict,
    payment_id: str,
    payload: RazorpayCheckoutConfirmRequest,
) -> ShopPaymentCompleteResponse:
    """Verify Checkout handler signature, then mark paid and fulfill."""
    require_razorpay()
    document = _get_payment_for_user(user, payment_id)
    order_id = payload.razorpay_order_id.strip()
    payment_rz = payload.razorpay_payment_id.strip()
    signature = payload.razorpay_signature.strip()

    if not verify_checkout_signature(
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_rz,
        razorpay_signature=signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Razorpay signature verification failed",
        )

    stored_order = str(document.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Razorpay order id does not match this payment",
        )

    document = _mark_paid_from_razorpay(
        document,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_rz,
        source="checkout",
    )
    return fulfill_paid_payment(user, document)


@router.get("/razorpay/status")
def razorpay_status() -> dict[str, bool]:
    """Public readiness probe for Frontweb (key id presence only; secret never returned)."""
    return {
        "configured": razorpay_configured(),
        "webhook_configured": webhook_configured(),
    }


@router.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """
    Server-side confirmation independent of the browser.
    Subscribe to payment.captured and payment.failed in the Razorpay dashboard.
    Requires RAZORPAY_WEBHOOK_SECRET.
    """
    if not webhook_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Razorpay webhook secret is not configured (RAZORPAY_WEBHOOK_SECRET).",
        )

    raw = await request.body()
    if not verify_webhook_signature(raw_body=raw, signature_header=x_razorpay_signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception as parse_exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook JSON",
        ) from parse_exc

    if not isinstance(event, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON")

    event_name = str(event.get("event") or "").strip()
    payment_entity = (
        (event.get("payload") or {}).get("payment") or {}
    ).get("entity") or {}
    order_id = str(payment_entity.get("order_id") or "").strip()
    rz_payment_id = str(payment_entity.get("id") or "").strip()

    if not order_id:
        return {"status": "ignored", "reason": "missing_order_id"}

    document = shop_payments.find_one({"razorpay_order_id": order_id})
    if document is None:
        return {"status": "ignored", "reason": "unknown_order"}

    if event_name == "payment.captured":
        document = _mark_paid_from_razorpay(
            document,
            razorpay_order_id=order_id,
            razorpay_payment_id=rz_payment_id or str(document.get("razorpay_payment_id") or "webhook"),
            source="webhook",
        )
        fulfill_paid_payment(_owner_user_for_payment(document), document)
        return {"status": "paid"}

    if event_name == "payment.failed":
        error = payment_entity.get("error_description") or payment_entity.get("error_reason")
        _mark_failed_from_razorpay(document, reason=str(error) if error else event_name, source="webhook")
        return {"status": "failed"}

    return {"status": "ignored", "reason": event_name or "unknown_event"}


@router.post("/{payment_id}/razorpay/order", response_model=RazorpayOrderResponse)
def create_razorpay_order_for_payment(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> RazorpayOrderResponse:
    """Create (or reuse) a Razorpay order for a pending shop payment — Standard Checkout step 1."""
    require_razorpay()
    document = _get_payment_for_user(current_user, payment_id)
    if ShopPaymentStatus(document["status"]) != ShopPaymentStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create Razorpay order for status {document['status']}",
        )

    amount_inr = int(document["amount_inr"])
    existing_order = str(document.get("razorpay_order_id") or "").strip()
    if existing_order and int(document.get("razorpay_amount_inr") or amount_inr) == amount_inr:
        order_id = existing_order
        amount_paise = amount_inr * 100
    else:
        order = create_order(
            amount_inr=amount_inr,
            receipt=str(document["_id"]),
            notes={
                "payment_id": str(document["_id"]),
                "store_id": str(document["store_id"]),
                "kind": str(document["kind"]),
            },
        )
        order_id = str(order["id"])
        amount_paise = int(order.get("amount") or amount_inr * 100)
        now = utc_now()
        updates = {
            "razorpay_order_id": order_id,
            "razorpay_amount_inr": amount_inr,
            "updated_at": now,
        }
        shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
        document.update(updates)

    prefill: dict[str, str] = {}
    phone = str(current_user.get("phone_number") or "").strip()
    email = str(current_user.get("email") or "").strip()
    name = str(current_user.get("display_name") or "").strip()
    if phone:
        prefill["contact"] = phone.lstrip("+") if phone.startswith("+91") else phone
    if email:
        prefill["email"] = email
    if name:
        prefill["name"] = name

    return RazorpayOrderResponse(
        payment_id=str(document["_id"]),
        key_id=RAZORPAY_KEY_ID,
        order_id=order_id,
        amount_paise=amount_paise,
        currency=str(document.get("currency") or "INR"),
        description=str(document.get("description") or "Junction plan payment"),
        prefill=prefill,
    )


@router.post("/{payment_id}/razorpay/confirm", response_model=ShopPaymentCompleteResponse)
def confirm_razorpay_payment(
    payment_id: str,
    payload: RazorpayCheckoutConfirmRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPaymentCompleteResponse:
    """
    Verify Checkout success signature (HMAC SHA256) then activate plan/packs.
    Prefer this after the Angular handler; webhook remains the reliability backstop.
    """
    return confirm_razorpay_checkout(current_user, payment_id, payload)


@router.post("/prepare", response_model=ShopPayment, status_code=status.HTTP_201_CREATED)
def prepare_shop_payment(
    payload: PreparePurchaseRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    """
    Create one pending Razorpay bill for plan-only, pack-only, or plan + packs.
    Amount includes 18% GST per line (plan and packs taxed separately, then summed).
    """
    return create_prepared_purchase(current_user, payload)


@router.get("", response_model=list[ShopPayment])
def list_shop_payments(
    current_user: Annotated[dict, Depends(get_current_user)],
    store_id: Annotated[str, Query(min_length=1, max_length=80)],
) -> list[ShopPayment]:
    """List plan/pack payment attempts for a shop (hides dismissed rows)."""
    _require_shop_access(current_user, store_id.strip())
    documents = shop_payments.find(
        {"store_id": store_id.strip(), "dismissed_at": {"$exists": False}}
    ).sort("created_at", -1)
    return [serialize_payment(doc) for doc in documents]


@router.post("/clear")
def clear_shop_payments(
    current_user: Annotated[dict, Depends(get_current_user)],
    store_id: Annotated[str, Query(min_length=1, max_length=80)],
) -> dict[str, int]:
    """
    Clear unpaid noise for a shop: cancel pending, dismiss failed/cancelled.
    Paid rows are left alone.
    """
    store = store_id.strip()
    _require_shop_access(current_user, store)
    now = utc_now()
    cancelled = shop_payments.update_many(
        {
            "store_id": store,
            "status": ShopPaymentStatus.pending.value,
            "dismissed_at": {"$exists": False},
        },
        {"$set": {"status": ShopPaymentStatus.cancelled.value, "updated_at": now}},
    )
    dismissed = shop_payments.update_many(
        {
            "store_id": store,
            "status": {
                "$in": [
                    ShopPaymentStatus.failed.value,
                    ShopPaymentStatus.cancelled.value,
                ]
            },
            "dismissed_at": {"$exists": False},
        },
        {"$set": {"dismissed_at": now, "updated_at": now}},
    )
    return {
        "cancelled": int(cancelled.modified_count),
        "dismissed": int(dismissed.modified_count),
    }


@router.get("/{payment_id}", response_model=ShopPayment)
def get_payment(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    document = _get_payment_for_user(current_user, payment_id)
    if document.get("dismissed_at") is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return serialize_payment(document)


@router.post("/{payment_id}/complete", response_model=ShopPaymentCompleteResponse)
def mark_payment_complete(
    payment_id: str,
    payload: CompletePaymentRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPaymentCompleteResponse:
    """
    Mark a pending shop purchase as paid and activate the plan or product packs.
    Call this after the payment gateway (or cash/UPI) succeeds. Products can then be added.
    """
    return complete_payment(current_user, payment_id, payload)


@router.post("/{payment_id}/fail", response_model=ShopPayment)
def mark_payment_failed(
    payment_id: str,
    payload: FailPaymentRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    document = _get_payment_for_user(current_user, payment_id)
    if ShopPaymentStatus(document["status"]) != ShopPaymentStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot fail a payment in status {document['status']}",
        )
    now = utc_now()
    updates = {
        "status": ShopPaymentStatus.failed.value,
        "updated_at": now,
        "failure_reason": (payload.reason or "").strip() or None,
    }
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return serialize_payment(document)


@router.post("/{payment_id}/cancel", response_model=ShopPayment)
def cancel_shop_payment(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    """Cancel a pending payment so it leaves the active queue."""
    document = _get_payment_for_user(current_user, payment_id)
    if ShopPaymentStatus(document["status"]) != ShopPaymentStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a payment in status {document['status']}",
        )
    now = utc_now()
    updates = {
        "status": ShopPaymentStatus.cancelled.value,
        "updated_at": now,
        "dismissed_at": now,
    }
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return serialize_payment(document)


@router.post("/{payment_id}/dismiss", response_model=ShopPayment)
def dismiss_shop_payment(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    """Remove a pending/failed/cancelled payment from the queue (paid rows stay)."""
    document = _get_payment_for_user(current_user, payment_id)
    status_value = ShopPaymentStatus(document["status"])
    if status_value == ShopPaymentStatus.paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paid payments cannot be deleted",
        )
    now = utc_now()
    updates: dict = {"dismissed_at": now, "updated_at": now}
    if status_value == ShopPaymentStatus.pending:
        updates["status"] = ShopPaymentStatus.cancelled.value
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return serialize_payment(document)
