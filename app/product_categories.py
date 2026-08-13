"""Product category catalog for back-office / junction.today pickers."""

from pydantic import BaseModel


class ProductCategoryInfo(BaseModel):
    value: str
    label: str
    group: str | None = None
    description: str


def _cat(value: str, label: str, description: str, group: str | None = None) -> ProductCategoryInfo:
    return ProductCategoryInfo(value=value, label=label, group=group, description=description)


PRODUCT_CATEGORIES: list[ProductCategoryInfo] = [
    _cat("grocery", "Grocery", "Staples, provisions, and packaged foods", "food"),
    _cat("dairy", "Dairy", "Milk, curd, cheese, and dairy products", "food"),
    _cat("bakery", "Bakery", "Bread, cakes, and baked goods", "food"),
    _cat("snacks", "Snacks", "Chips, namkeen, and ready snacks", "food"),
    _cat("beverages", "Beverages", "Soft drinks, juices, tea, and coffee", "food"),
    _cat("fresh_produce", "Fresh produce", "Fruits and vegetables", "food"),
    _cat("frozen", "Frozen", "Frozen foods and ice cream", "food"),
    _cat("personal_care", "Personal care", "Soap, shampoo, and hygiene", "health"),
    _cat("health", "Health", "OTC medicines and wellness", "health"),
    _cat("household", "Household", "Cleaning and home supplies", "home"),
    _cat("electronics", "Electronics", "Gadgets and accessories", "electronics"),
    _cat("mobile", "Mobile accessories", "Chargers, cases, and earphones", "electronics"),
    _cat("fashion", "Fashion", "Apparel and wearables", "fashion"),
    _cat("footwear", "Footwear", "Shoes and sandals", "fashion"),
    _cat("stationery", "Stationery", "Pens, paper, and office supplies", "general"),
    _cat("toys", "Toys", "Toys and kids items", "general"),
    _cat("pet", "Pet care", "Pet food and accessories", "general"),
    _cat("hardware", "Hardware", "Tools and hardware supplies", "home"),
    _cat("services", "Services", "Service SKUs and add-ons", "services"),
    _cat("other", "Other", "Uncategorized or miscellaneous", "general"),
]


def product_category_values() -> set[str]:
    return {row.value for row in PRODUCT_CATEGORIES}
