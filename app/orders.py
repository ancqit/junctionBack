from datetime import datetime, timezone
from enum import Enum
import re
import secrets

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .database import orders
from .utils import parse_object_id

router = APIRouter(prefix="/orders", tags=["orders"])


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

    @field_validator("customer_name")
    @classmethod
    def strip_customer_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("customer_name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_subtotal(self) -> "OrderCreate":
        items_subtotal = round(sum(item.line_total for item in self.items), 2)
        if abs(items_subtotal - self.billing.subtotal) > 0.01:
            raise ValueError("billing.subtotal must match the sum of order line items")
        return self


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


@router.get("", response_model=list[Order])
def list_orders(
    store_id: str | None = Query(default=None, min_length=1, max_length=80),
    customer_name: str | None = Query(default=None, min_length=1, max_length=160),
    status: OrderStatus | None = None,
) -> list[Order]:
    documents = orders.find(build_list_query(store_id, customer_name, status)).sort("created_at", -1)
    return [serialize_order(document) for document in documents]


@router.get("/by-name/{customer_name}", response_model=list[Order])
def get_orders_by_name(
    customer_name: str,
    store_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> list[Order]:
    name = customer_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="customer_name must not be blank")
    query = build_list_query(store_id, name, None)
    documents = orders.find(query).sort("created_at", -1)
    results = [serialize_order(document) for document in documents]
    if not results:
        raise HTTPException(status_code=404, detail="No orders found for this customer name")
    return results


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: str) -> Order:
    document = orders.find_one({"_id": parse_object_id(order_id, "Order")})
    if document is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(document)


@router.post("", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate) -> Order:
    orders.create_index("order_number", unique=True)
    orders.create_index([("store_id", 1), ("customer_name", 1)])

    now = datetime.now(timezone.utc)
    document = {
        **payload.model_dump(mode="json"),
        "order_number": generate_order_number(),
        "created_at": now,
        "updated_at": now,
    }
    document["items"] = [serialize_line_item(item) for item in document["items"]]
    result = orders.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_order(document)


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: str) -> Response:
    result = orders.delete_one({"_id": parse_object_id(order_id, "Order")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
