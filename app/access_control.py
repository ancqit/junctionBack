"""Shared authorization helpers for store-scoped resources."""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from .database import employees, orders, products, shops
from .login import get_current_user
from .roles import UserRole, get_user_role
from .utils import parse_object_id


def ensure_shop_access(user: dict, shop: dict) -> None:
    """Admins may access any shop; owners only their own."""
    if get_user_role(user) == UserRole.admin:
        return
    if str(shop.get("owner_user_id")) != str(user["_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this shop")


def get_shop_by_store_id(store_id: str) -> dict:
    shop = shops.find_one({"_id": parse_object_id(store_id, "Shop")})
    if shop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    return shop


def require_store_access(user: dict, store_id: str) -> dict:
    shop = get_shop_by_store_id(store_id)
    ensure_shop_access(user, shop)
    return shop


def allowed_store_ids(user: dict) -> list[str] | None:
    """Return store IDs the user may access, or None when admin (all stores)."""
    if get_user_role(user) == UserRole.admin:
        return None
    return [str(shop["_id"]) for shop in shops.find({"owner_user_id": str(user["_id"])})]


def apply_store_filter(query: dict, user: dict, store_id: str | None) -> dict:
    """Restrict a Mongo query to stores the user can access."""
    if store_id is not None:
        require_store_access(user, store_id.strip())
        query["store_id"] = store_id.strip()
        return query

    scope = allowed_store_ids(user)
    if scope is None:
        return query
    query["store_id"] = {"$in": scope or ["__none__"]}
    return query


def get_product_or_404(product_id: str) -> dict:
    document = products.find_one({"_id": parse_object_id(product_id, "Product")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return document


def require_product_access(user: dict, product_id: str) -> dict:
    document = get_product_or_404(product_id)
    require_store_access(user, document["store_id"])
    return document


def get_employee_or_404(employee_id: str) -> dict:
    document = employees.find_one({"_id": parse_object_id(employee_id, "Employee")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return document


def require_employee_access(user: dict, employee_id: str) -> dict:
    document = get_employee_or_404(employee_id)
    require_store_access(user, document["store_id"])
    return document


def get_order_or_404(order_id: str) -> dict:
    document = orders.find_one({"_id": parse_object_id(order_id, "Order")})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return document


def require_order_access(user: dict, order_id: str) -> dict:
    document = get_order_or_404(order_id)
    require_store_access(user, document["store_id"])
    return document


AuthenticatedUser = Annotated[dict, Depends(get_current_user)]
