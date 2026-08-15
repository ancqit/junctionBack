"""Shop plan and product-pack purchases — activate only after payment completes."""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .access_control import require_store_access
from .database import products, shop_payments
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
    PROVIDER,
    create_razorpay_order,
    extract_captured_payment,
    parse_webhook_payload,
    razorpay_configured,
    razorpay_key_id,
    verify_payment_signature,
    verify_webhook_signature,
)
from .roles import UserRole, get_user_role
from .utils import parse_object_id

router = APIRouter(prefix="/payments", tags=["payments"])


class PaymentKind(str, Enum):
    plan = "plan"
    product_pack = "product_pack"


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
    plan_type: PlanType | None = None
    packs: int | None = None
    slots: int | None = None
    description: str
    payment_method: PaymentMethod | None = None
    payment_reference: str | None = None
    provider: str | None = None
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
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


class CompletePaymentRequest(BaseModel):
    payment_method: PaymentMethod | None = None
    payment_reference: str | None = Field(default=None, max_length=200)


class FailPaymentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class VerifyRazorpayPaymentRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=80)
    razorpay_payment_id: str = Field(min_length=1, max_length=80)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class RazorpayCheckoutResponse(BaseModel):
    payment_id: str
    provider: str = PROVIDER
    key_id: str
    order_id: str
    amount_inr: int
    amount_paise: int
    currency: str = "INR"
    description: str
    store_id: str
    kind: PaymentKind
    plan_type: PlanType | None = None
    packs: int | None = None
    name: str | None = None
    email: str | None = None
    contact: str | None = None


class ShopPaymentCompleteResponse(BaseModel):
    payment: ShopPayment
    plan: PlanSummary | None = None
    product_bucket: ProductBucketResponse | None = None
    message: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_payment_indexes() -> None:
    shop_payments.create_index("store_id")
    shop_payments.create_index("owner_user_id")
    shop_payments.create_index([("store_id", 1), ("status", 1)])


def serialize_payment(document: dict) -> ShopPayment:
    return ShopPayment(
        id=str(document["_id"]),
        store_id=document["store_id"],
        owner_user_id=document["owner_user_id"],
        kind=PaymentKind(document["kind"]),
        status=ShopPaymentStatus(document["status"]),
        amount_inr=int(document["amount_inr"]),
        currency=document.get("currency", "INR"),
        plan_type=PlanType(document["plan_type"]) if document.get("plan_type") else None,
        packs=document.get("packs"),
        slots=document.get("slots"),
        description=document["description"],
        payment_method=PaymentMethod(document["payment_method"]) if document.get("payment_method") else None,
        payment_reference=document.get("payment_reference"),
        provider=document.get("provider"),
        provider_order_id=document.get("provider_order_id"),
        provider_payment_id=document.get("provider_payment_id"),
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
    if plan_type == PlanType.free_trial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Free trial starts automatically when the shop is created; no payment needed.",
        )

    shop = _require_shop_access(user, store_id)
    details = PLAN_CATALOG[plan_type.value]
    amount = int(details["price_inr"])
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This plan does not require payment. Contact support if activation is stuck.",
        )

    now = utc_now()
    _ensure_payment_indexes()
    # Cancel prior pending plan purchases for this shop so only one is active.
    shop_payments.update_many(
        {
            "store_id": store_id,
            "kind": PaymentKind.plan.value,
            "status": ShopPaymentStatus.pending.value,
        },
        {"$set": {"status": ShopPaymentStatus.cancelled.value, "updated_at": now}},
    )

    document = {
        "store_id": store_id,
        "owner_user_id": str(shop.get("owner_user_id") or user["_id"]),
        "kind": PaymentKind.plan.value,
        "status": ShopPaymentStatus.pending.value,
        "amount_inr": amount,
        "currency": "INR",
        "plan_type": plan_type.value,
        "packs": None,
        "slots": None,
        "description": f"{details['name']} plan for shop ({details['max_products']} products / 1 year)",
        "payment_method": None,
        "payment_reference": None,
        "provider": PROVIDER if razorpay_configured() else None,
        "provider_order_id": None,
        "provider_payment_id": None,
        "created_at": now,
        "updated_at": now,
        "paid_at": None,
        "fulfilled_at": None,
    }
    result = shop_payments.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_payment(document)


def create_pack_purchase(user: dict, store_id: str, packs: int) -> ShopPayment:
    store_id = store_id.strip()
    shop = _require_shop_access(user, store_id)
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

    products_count = products.count_documents({"store_id": store_id})
    if products_count < summary.max_products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This shop still has plan allowance remaining "
                f"({products_count}/{summary.max_products}). "
                "Buy packs after the plan products are used."
            ),
        )

    amount = packs * BUCKET_PACK_PRICE_INR
    slots = packs * BUCKET_PACK_SIZE
    now = utc_now()
    _ensure_payment_indexes()
    document = {
        "store_id": store_id,
        "owner_user_id": str(shop.get("owner_user_id") or user["_id"]),
        "kind": PaymentKind.product_pack.value,
        "status": ShopPaymentStatus.pending.value,
        "amount_inr": amount,
        "currency": "INR",
        "plan_type": None,
        "packs": packs,
        "slots": slots,
        "description": f"{packs} product pack(s) (+{slots} products) for shop",
        "payment_method": None,
        "payment_reference": None,
        "provider": PROVIDER if razorpay_configured() else None,
        "provider_order_id": None,
        "provider_payment_id": None,
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
    """Apply plan or packs after payment is marked paid. Idempotent if already fulfilled."""
    store_id = document["store_id"]
    kind = PaymentKind(document["kind"])
    now = utc_now()

    if document.get("fulfilled_at") is not None and document.get("status") == ShopPaymentStatus.paid.value:
        plan = build_shop_plan_summary(get_shop_document(store_id))
        bucket = None
        if kind == PaymentKind.product_pack:
            bucket = build_product_bucket(user, store_id)
        return ShopPaymentCompleteResponse(
            payment=serialize_payment(document),
            plan=plan,
            product_bucket=bucket,
            message="Payment already completed; plan/products capacity is active.",
        )

    if kind == PaymentKind.plan:
        plan_type = PlanType(document["plan_type"])
        plan = select_plan_for_shop(store_id, plan_type)
        shop_payments.update_one(
            {"_id": document["_id"]},
            {"$set": {"fulfilled_at": now, "updated_at": now}},
        )
        document["fulfilled_at"] = now
        document["updated_at"] = now
        return ShopPaymentCompleteResponse(
            payment=serialize_payment(document),
            plan=plan,
            product_bucket=None,
            message=(
                f"{plan.name} is now active for this shop. "
                f"You can add up to {plan.max_products} products."
            ),
        )

    packs = int(document["packs"])
    apply_bucket_packs(store_id, packs)
    shop_payments.update_one(
        {"_id": document["_id"]},
        {"$set": {"fulfilled_at": now, "updated_at": now}},
    )
    document["fulfilled_at"] = now
    document["updated_at"] = now
    bucket = build_product_bucket(user, store_id)
    plan = build_shop_plan_summary(get_shop_document(store_id))
    return ShopPaymentCompleteResponse(
        payment=serialize_payment(document),
        plan=plan,
        product_bucket=bucket,
        message=f"Added {packs * BUCKET_PACK_SIZE} product slots. You can add more products now.",
    )


def complete_payment(user: dict, payment_id: str, payload: CompletePaymentRequest) -> ShopPaymentCompleteResponse:
    document = _get_payment_for_user(user, payment_id)
    status_value = ShopPaymentStatus(document["status"])

    if status_value == ShopPaymentStatus.failed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This payment has failed")
    if status_value == ShopPaymentStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This payment was cancelled")

    # When Razorpay is live, owners must use checkout + verify (or webhook).
    # Admins may still mark paid for support / cash exceptions.
    if (
        status_value == ShopPaymentStatus.pending
        and razorpay_configured()
        and get_user_role(user) != UserRole.admin
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Complete this purchase via Razorpay checkout. "
                "Call POST /payments/{id}/checkout, then POST /payments/{id}/verify."
            ),
        )

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


def _mark_provider_paid(
    document: dict,
    *,
    provider_order_id: str,
    provider_payment_id: str,
    payment_method: PaymentMethod | None = PaymentMethod.upi,
) -> dict:
    now = utc_now()
    updates: dict[str, Any] = {
        "status": ShopPaymentStatus.paid.value,
        "paid_at": now,
        "updated_at": now,
        "provider": PROVIDER,
        "provider_order_id": provider_order_id,
        "provider_payment_id": provider_payment_id,
        "payment_reference": provider_payment_id,
    }
    if payment_method is not None:
        updates["payment_method"] = payment_method.value
    shop_payments.update_one({"_id": document["_id"]}, {"$set": updates})
    document.update(updates)
    return document


def create_checkout_session(user: dict, payment_id: str) -> RazorpayCheckoutResponse:
    document = _get_payment_for_user(user, payment_id)
    if ShopPaymentStatus(document["status"]) != ShopPaymentStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot checkout a payment in status {document['status']}",
        )

    amount_inr = int(document["amount_inr"])
    payment_oid = str(document["_id"])
    existing_order = document.get("provider_order_id")
    if existing_order and document.get("provider") == PROVIDER:
        order_id = str(existing_order)
    else:
        order = create_razorpay_order(
            amount_inr=amount_inr,
            receipt=payment_oid,
            notes={
                "junction_payment_id": payment_oid,
                "store_id": document["store_id"],
                "kind": document["kind"],
            },
        )
        order_id = str(order["id"])
        now = utc_now()
        shop_payments.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "provider": PROVIDER,
                    "provider_order_id": order_id,
                    "updated_at": now,
                }
            },
        )
        document["provider"] = PROVIDER
        document["provider_order_id"] = order_id

    return RazorpayCheckoutResponse(
        payment_id=payment_oid,
        key_id=razorpay_key_id(),
        order_id=order_id,
        amount_inr=amount_inr,
        amount_paise=amount_inr * 100,
        currency=document.get("currency", "INR"),
        description=document["description"],
        store_id=document["store_id"],
        kind=PaymentKind(document["kind"]),
        plan_type=PlanType(document["plan_type"]) if document.get("plan_type") else None,
        packs=document.get("packs"),
        name=(user.get("name") or user.get("full_name") or None),
        email=user.get("email"),
        contact=user.get("phone") or user.get("mobile"),
    )


def verify_razorpay_payment(
    user: dict,
    payment_id: str,
    payload: VerifyRazorpayPaymentRequest,
) -> ShopPaymentCompleteResponse:
    document = _get_payment_for_user(user, payment_id)
    status_value = ShopPaymentStatus(document["status"])

    if status_value in {ShopPaymentStatus.failed, ShopPaymentStatus.cancelled}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot verify a payment in status {document['status']}",
        )

    expected_order = document.get("provider_order_id")
    if expected_order and expected_order != payload.razorpay_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Razorpay order does not match this payment.",
        )

    verify_payment_signature(
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    )

    if status_value == ShopPaymentStatus.pending:
        document = _mark_provider_paid(
            document,
            provider_order_id=payload.razorpay_order_id,
            provider_payment_id=payload.razorpay_payment_id,
        )

    return fulfill_paid_payment(user, document)


def fulfill_from_provider_order(
    *,
    provider_order_id: str,
    provider_payment_id: str,
) -> ShopPaymentCompleteResponse | None:
    document = shop_payments.find_one({"provider_order_id": provider_order_id})
    if document is None:
        return None

    status_value = ShopPaymentStatus(document["status"])
    if status_value in {ShopPaymentStatus.failed, ShopPaymentStatus.cancelled}:
        return None

    if status_value == ShopPaymentStatus.pending:
        document = _mark_provider_paid(
            document,
            provider_order_id=provider_order_id,
            provider_payment_id=provider_payment_id,
        )

    # Fulfillment uses store owner context only for bucket serialization access.
    owner = {"_id": document["owner_user_id"], "role": UserRole.admin.value}
    return fulfill_paid_payment(owner, document)


@router.get("", response_model=list[ShopPayment])
def list_shop_payments(
    current_user: Annotated[dict, Depends(get_current_user)],
    store_id: Annotated[str, Query(min_length=1, max_length=80)],
) -> list[ShopPayment]:
    """List plan/pack payment attempts for a shop."""
    _require_shop_access(current_user, store_id.strip())
    documents = shop_payments.find({"store_id": store_id.strip()}).sort("created_at", -1)
    return [serialize_payment(doc) for doc in documents]


@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request) -> dict[str, str]:
    """
    Razorpay webhook (payment.captured). Verifies signature and fulfills the shop payment.
    Configure this URL in the Razorpay dashboard.
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    verify_webhook_signature(body=body, signature=signature)
    payload = parse_webhook_payload(body)
    event, order_id, payment_id = extract_captured_payment(payload)

    if event not in {"payment.captured", "payment.authorized"}:
        return {"status": "ignored", "event": event or "unknown"}
    if not order_id or not payment_id:
        return {"status": "ignored", "reason": "missing_ids"}

    result = fulfill_from_provider_order(
        provider_order_id=order_id,
        provider_payment_id=payment_id,
    )
    if result is None:
        return {"status": "ignored", "reason": "payment_not_found"}
    return {"status": "ok", "payment_id": result.payment.id}


@router.get("/{payment_id}", response_model=ShopPayment)
def get_payment(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPayment:
    return serialize_payment(_get_payment_for_user(current_user, payment_id))


@router.post("/{payment_id}/checkout", response_model=RazorpayCheckoutResponse)
def start_razorpay_checkout(
    payment_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> RazorpayCheckoutResponse:
    """Create (or reuse) a Razorpay order for a pending shop payment."""
    return create_checkout_session(current_user, payment_id)


@router.post("/{payment_id}/verify", response_model=ShopPaymentCompleteResponse)
def verify_razorpay_checkout(
    payment_id: str,
    payload: VerifyRazorpayPaymentRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPaymentCompleteResponse:
    """
    Verify Razorpay Checkout success callback, mark paid, and activate plan/packs.
    Prefer this after the client handler; webhook is the backup path.
    """
    return verify_razorpay_payment(current_user, payment_id, payload)


@router.post("/{payment_id}/complete", response_model=ShopPaymentCompleteResponse)
def mark_payment_complete(
    payment_id: str,
    payload: CompletePaymentRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> ShopPaymentCompleteResponse:
    """
    Mark a pending shop purchase as paid and activate the plan or product packs.
    When Razorpay is configured, only admins may use this; owners use checkout + verify.
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
