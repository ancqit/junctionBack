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
    # Food & grocery
    _cat("grocery", "Grocery", "Staples, provisions, and packaged foods", "food"),
    _cat("dairy", "Dairy", "Milk, curd, cheese, and dairy products", "food"),
    _cat("bakery", "Bakery", "Bread, cakes, and baked goods", "food"),
    _cat("snacks", "Snacks", "Chips, namkeen, and ready snacks", "food"),
    _cat("beverages", "Beverages", "Soft drinks, juices, tea, and coffee", "food"),
    _cat("fresh_produce", "Fresh produce", "Fruits and vegetables", "food"),
    _cat("frozen", "Frozen", "Frozen foods and ice cream", "food"),
    _cat("spices", "Spices & masala", "Whole and ground spices", "food"),
    _cat("oils", "Oils & ghee", "Cooking oils and ghee", "food"),
    _cat("pulses", "Pulses & dals", "Lentils and legumes", "food"),
    _cat("rice_flour", "Rice & flour", "Rice, atta, and millets", "food"),
    _cat("sweets", "Sweets & mithai", "Indian sweets and desserts", "food"),
    _cat("ready_to_eat", "Ready to eat", "Instant meals and mixes", "food"),
    _cat("meat", "Meat & seafood", "Fresh or frozen meat and fish", "food"),
    _cat("organic", "Organic", "Organic groceries and produce", "food"),
    # Health & beauty
    _cat("personal_care", "Personal care", "Soap, shampoo, and hygiene", "health"),
    _cat("health", "Health", "OTC medicines and wellness", "health"),
    _cat("ayurveda", "Ayurveda", "Ayurvedic products", "health"),
    _cat("cosmetics", "Cosmetics", "Makeup and beauty", "health"),
    _cat("baby_care", "Baby care", "Diapers, formula, and baby products", "health"),
    # Home
    _cat("household", "Household", "Cleaning and home supplies", "home"),
    _cat("kitchenware", "Kitchenware", "Utensils and cookware", "home"),
    _cat("home_decor", "Home décor", "Decor and furnishings", "home"),
    _cat("hardware", "Hardware", "Tools and hardware supplies", "home"),
    _cat("electrical", "Electrical", "Switches, bulbs, and wiring", "home"),
    # Electronics
    _cat("electronics", "Electronics", "Gadgets and accessories", "electronics"),
    _cat("mobile", "Mobile accessories", "Chargers, cases, and earphones", "electronics"),
    _cat("appliances", "Appliances", "Small home appliances", "electronics"),
    # Fashion
    _cat("fashion", "Fashion", "Apparel and wearables", "fashion"),
    _cat("footwear", "Footwear", "Shoes and sandals", "fashion"),
    _cat("jewellery", "Jewellery", "Fashion and traditional jewellery", "fashion"),
    _cat("bags", "Bags & luggage", "Bags, wallets, and luggage", "fashion"),
    # General
    _cat("stationery", "Stationery", "Pens, paper, and office supplies", "general"),
    _cat("toys", "Toys", "Toys and kids items", "general"),
    _cat("pet", "Pet care", "Pet food and accessories", "general"),
    _cat("books", "Books & magazines", "Books, comics, and magazines", "general"),
    _cat("sports", "Sports & fitness", "Sports gear and fitness", "general"),
    _cat("automotive", "Automotive", "Car and bike accessories", "general"),
    _cat("agriculture", "Agriculture", "Seeds, feed, and farm supplies", "general"),
    _cat("services", "Services", "Service SKUs and add-ons", "services"),
    # Software & digital (for developers / agencies)
    _cat("web_application", "Web application", "Web apps, portals, and SaaS UIs", "software"),
    _cat("mobile_app", "Mobile app", "iOS, Android, and cross-platform apps", "software"),
    _cat("saas", "SaaS", "Subscription software and cloud tools", "software"),
    _cat("api_services", "API & integrations", "APIs, webhooks, and system integrations", "software"),
    _cat("devops", "DevOps & hosting", "CI/CD, cloud hosting, and infrastructure", "software"),
    _cat("ui_ux", "UI / UX design", "Product design, wireframes, and prototypes", "software"),
    _cat("it_services", "IT services", "Support, maintenance, and consulting", "software"),
    _cat("digital_marketing", "Digital marketing", "SEO, ads, and growth services", "software"),
    _cat("other", "Other", "Uncategorized or miscellaneous", "general"),
]


def product_category_values() -> set[str]:
    return {row.value for row in PRODUCT_CATEGORIES}
