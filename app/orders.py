from datetime import datetime, timezone
from enum import Enum
import re
import secrets

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from pymongo import ReturnDocument

from .access_control import (
    AuthenticatedUser,
    apply_store_filter,
    get_product_or_404,
    get_shop_by_store_id,
    require_order_access,
    require_store_access,
)
from .database import orders
from .rate_limit import RATE_LIMIT_GUEST_ORDERS, limiter
from .session import CatalogReader, is_junction_session
from .utils import parse_object_id

router = APIRouter(prefix="/orders", tags=["orders"])

JUNCTION_TODAY_SOURCE = "junction.today"


class OrderStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"


class PaymentStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"


class PaymentMethod(str, Enum):
    cash = "cash"
    card = "card"
    upi = "upi"
    bank_transfer = "bank_transfer"
    other = "other"


class BillingAddress(BaseModel):
    line1: str = Field(min_length=1, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(default="IN", min_length=2, max_length=2)

    @field_validator("line1", "city", "state", "postal_code")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class OrderLineItem(BaseModel):
    product_id: str | None = None
    product_name: str = Field(min_length=1, max_length=160)
    sku: str | None = Field(default=None, max_length=64)
    quantity: int = Field(ge=1)
    unit_price: float = Field(ge=0)

    @field_validator("product_name")
    @classmethod
    def strip_product_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be blank")
        return value

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


class BillingDetails(BaseModel):
    subtotal: float = Field(ge=0)
    tax_amount: float = Field(default=0, ge=0)
    discount_amount: float = Field(default=0, ge=0)
    shipping_amount: float = Field(default=0, ge=0)
    total_amount: float = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    payment_method: PaymentMethod
    payment_status: PaymentStatus = PaymentStatus.pending
    billing_address: BillingAddress | None = None

    @model_validator(mode="after")
    def validate_total(self) -> "BillingDetails":
        expected_total = round(
            self.subtotal + self.tax_amount + self.shipping_amount - self.discount_amount,
            2,
        )
        if abs(expected_total - self.total_amount) > 0.01:
            raise ValueError("total_amount must equal subtotal + tax + shipping - discount")
        return self


class OrderCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    customer_name: str = Field(min_length=1, max_length=160)
    customer_phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    customer_email: EmailStr | None = None
    items: list[OrderLineItem] = Field(min_length=1, max_length=100)
    billing: BillingDetails
    status: OrderStatus = OrderStatus.pending
    notes: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(
        default=None,
        max_length=40,
        description='Optional origin, e.g. "junction.today"',
    )

    @field_validator("customer_name")
    @classmethod
    def strip_customer_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("customer_name must not be blank")
        return value

    @field_validator("source")
    @classmethod
    def strip_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_subtotal(self) -> "OrderCreate":
        items_subtotal = round(sum(item.line_total for item in self.items), 2)
        if abs(items_subtotal - self.billing.subtotal) > 0.01:
            raise ValueError("billing.subtotal must match the sum of order line items")
        return self


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class Order(OrderCreate):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    order_number: str
    created_at: datetime
    updated_at: datetime


def generate_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3).upper()
    return f"ORD-{timestamp}-{suffix}"


def serialize_line_item(item: dict) -> dict:
    quantity = item["quantity"]
    unit_price = item["unit_price"]
    return {
        **item,
        "line_total": round(quantity * unit_price, 2),
    }


def serialize_order(document: dict) -> Order:
    items = [serialize_line_item(item) for item in document["items"]]
    return Order(
        id=str(document["_id"]),
        order_number=document["order_number"],
        store_id=document["store_id"],
        customer_name=document["customer_name"],
        customer_phone=document.get("customer_phone"),
        customer_email=document.get("customer_email"),
        items=items,
        billing=document["billing"],
        status=document.get("status", OrderStatus.pending.value),
        notes=document.get("notes"),
        source=document.get("source"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def build_list_query(
    store_id: str | None,
    customer_name: str | None,
    status: OrderStatus | None,
) -> dict:
    query: dict = {}
    if store_id:
        query["store_id"] = store_id.strip()
    if customer_name:
        escaped = re.escape(customer_name.strip())
        query["customer_name"] = {"$regex": escaped, "$options": "i"}
    if status:
        query["status"] = status.value
    return query


def validate_order_products_for_store(store_id: str, items: list[OrderLineItem]) -> None:
    """When product_id is set, require the product to exist and belong to store_id."""
    for item in items:
        product_id = (item.product_id or "").strip()
        if not product_id:
            continue
        product = get_product_or_404(product_id)
        if str(product.get("store_id", "")).strip() != store_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product_id} does not belong to this store",
            )


@router.get("", response_model=list[Order])
def list_orders(
    current_user: AuthenticatedUser,
    store_id: str | None = Query(default=None, min_length=1, max_length=80),
    customer_name: str | None = Query(default=None, min_length=1, max_length=160),
    status: OrderStatus | None = None,
) -> list[Order]:
    query = build_list_query(store_id, customer_name, status)
    apply_store_filter(query, current_user, store_id)
    documents = orders.find(query).sort("created_at", -1)
    return [serialize_order(document) for document in documents]


@router.get("/by-name/{customer_name}", response_model=list[Order])
def get_orders_by_name(
    customer_name: str,
    current_user: AuthenticatedUser,
    store_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> list[Order]:
    name = customer_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="customer_name must not be blank")
    query = build_list_query(store_id, name, None)
    apply_store_filter(query, current_user, store_id)
    documents = orders.find(query).sort("created_at", -1)
    results = [serialize_order(document) for document in documents]
    if not results:
        raise HTTPException(status_code=404, detail="No orders found for this customer name")
    return results


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: str, current_user: AuthenticatedUser) -> Order:
    document = require_order_access(current_user, order_id)
    return serialize_order(document)


@router.post("", response_model=Order, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_GUEST_ORDERS)
def create_order(request: Request, payload: OrderCreate, auth: CatalogReader) -> Order:
    """
    Create an order.
    - Owner/admin JWT: must own the shop (or be admin).
    - junction.today session JWT: any real shop; no ownership check. Rate-limited.
    """
    store_id = payload.store_id.strip()
    if is_junction_session(auth):
        get_shop_by_store_id(store_id)
    else:
        require_store_access(auth["user"], store_id)

    validate_order_products_for_store(store_id, payload.items)

    orders.create_index("order_number", unique=True)
    orders.create_index([("store_id", 1), ("customer_name", 1)])
    orders.create_index([("store_id", 1), ("created_at", -1)])

    now = datetime.now(timezone.utc)
    data = payload.model_dump(mode="json")
    data["store_id"] = store_id
    if is_junction_session(auth) and not data.get("source"):
        data["source"] = JUNCTION_TODAY_SOURCE

    document = {
        **data,
        "order_number": generate_order_number(),
        "created_at": now,
        "updated_at": now,
    }
    document["items"] = [serialize_line_item(item) for item in document["items"]]
    result = orders.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_order(document)


@router.patch("/{order_id}", response_model=Order)
def update_order_status(
    order_id: str,
    payload: OrderStatusUpdate,
    current_user: AuthenticatedUser,
) -> Order:
    """Owner/admin: update order status (confirm / complete / cancel)."""
    document = require_order_access(current_user, order_id)
    updated = orders.find_one_and_update(
        {"_id": document["_id"]},
        {
            "$set": {
                "status": payload.status.value,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(updated)


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: str, current_user: AuthenticatedUser) -> Response:
    require_order_access(current_user, order_id)
    result = orders.delete_one({"_id": parse_object_id(order_id, "Order")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
