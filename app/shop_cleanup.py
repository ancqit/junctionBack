"""Remove all data owned by a shop when the owner deletes it."""

from .database import (
    employees,
    notices,
    orders,
    plan_applications,
    product_buckets,
    products,
    shop_payments,
    shops,
)
from .products import delete_stored_images, product_images_from_document
from .utils import parse_object_id


def delete_shop_cascade(shop_id: str) -> None:
    """
    Hard-delete a shop and every record scoped to it:
    products (and stored images), employees, orders, notices, product buckets,
    shop payments, and plan applications.
    """
    store_id = shop_id.strip()
    object_id = parse_object_id(store_id, "Shop")

    for document in products.find({"store_id": store_id}):
        delete_stored_images(product_images_from_document(document))
    products.delete_many({"store_id": store_id})

    employees.delete_many({"store_id": store_id})
    orders.delete_many({"store_id": store_id})
    notices.delete_many({"store_id": store_id})
    product_buckets.delete_many({"store_id": store_id})
    shop_payments.delete_many({"store_id": store_id})
    plan_applications.delete_many({"shop_id": store_id})
    shops.delete_one({"_id": object_id})
