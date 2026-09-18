"""Product-centric category taxonomy for back-office / junction.today pickers.

Distinct from shop types (`shop_types.py`): shop type describes the *store*;
these categories describe the *SKU* (rice under grocery, chargers under mobile, etc.).

Structure follows common retail taxonomies (Google Product Categories / Shopify-style):
department (`group`) → product type (`value`), shallow enough to pick fast.
Custom free-text categories remain allowed on create (not enum-locked).
"""

from pydantic import BaseModel


class ProductCategoryInfo(BaseModel):
    value: str
    label: str
    """Department key — used for grouped pickers (not the same as shop_type)."""
    group: str | None = None
    """Human label for the department section header."""
    group_label: str | None = None
    description: str


def _cat(
    value: str,
    label: str,
    description: str,
    group: str,
    group_label: str,
) -> ProductCategoryInfo:
    return ProductCategoryInfo(
        value=value,
        label=label,
        group=group,
        group_label=group_label,
        description=description,
    )


# Department labels (L1) — product-centric, not shop-type names.
_G_FOOD = ("food", "Food & Grocery")
_G_BEV = ("beverages", "Beverages")
_G_HEALTH = ("health", "Health & Beauty")
_G_BABY = ("baby", "Baby & Kids Care")
_G_HOME = ("home", "Home & Household")
_G_ELEC = ("electronics", "Electronics")
_G_APPL = ("appliances", "Appliances")
_G_FASHION = ("fashion", "Fashion & Apparel")
_G_FOOT = ("footwear", "Footwear")
_G_JEWEL = ("jewellery", "Jewellery & Accessories")
_G_SPORTS = ("sports", "Sports & Outdoors")
_G_STAT = ("stationery", "Stationery & Office")
_G_TOYS = ("toys", "Toys & Games")
_G_PET = ("pet", "Pet Supplies")
_G_AUTO = ("automotive", "Automotive")
_G_AGRI = ("agriculture", "Agriculture")
_G_SVC = ("services", "Services")
_G_SOFT = ("software", "Software & Digital")
_G_GEN = ("general", "General")


PRODUCT_CATEGORIES: list[ProductCategoryInfo] = [
    # —— Food & Grocery (staples as first-class product types) ——
    _cat("rice", "Rice", "Rice varieties and packs", *_G_FOOD),
    _cat("wheat_atta", "Wheat & atta", "Wheat, atta, maida, sooji", *_G_FOOD),
    _cat("millets", "Millets", "Ragi, jowar, bajra, and millet flours", *_G_FOOD),
    _cat("pulses", "Pulses & dals", "Lentils, beans, and legumes", *_G_FOOD),
    _cat("oils_ghee", "Oils & ghee", "Cooking oils, ghee, and vanaspati", *_G_FOOD),
    _cat("spices_masala", "Spices & masala", "Whole spices, powders, and blends", *_G_FOOD),
    _cat("salt_sugar", "Salt & sugar", "Salt, sugar, jaggery, and sweeteners", *_G_FOOD),
    _cat("dry_fruits", "Dry fruits & nuts", "Almonds, cashews, raisins, and mixes", *_G_FOOD),
    _cat("grocery_packaged", "Packaged grocery", "Sauces, pickles, canned and jarred foods", *_G_FOOD),
    _cat("breakfast_cereals", "Breakfast & cereals", "Oats, cornflakes, and breakfast mixes", *_G_FOOD),
    _cat("ready_to_eat", "Ready to eat / cook", "Instant meals, noodles, and mixes", *_G_FOOD),
    _cat("snacks_namkeen", "Snacks & namkeen", "Chips, namkeen, and savoury snacks", *_G_FOOD),
    _cat("sweets_mithai", "Sweets & mithai", "Indian sweets and dessert packs", *_G_FOOD),
    _cat("confectionery", "Confectionery", "Chocolates, candies, and biscuits", *_G_FOOD),
    _cat("bakery", "Bakery", "Bread, buns, cakes, and pastries", *_G_FOOD),
    _cat("dairy", "Dairy", "Milk, curd, paneer, cheese, and butter", *_G_FOOD),
    _cat("fresh_produce", "Fruits & vegetables", "Fresh produce", *_G_FOOD),
    _cat("meat_seafood", "Meat & seafood", "Fresh or frozen meat, poultry, and fish", *_G_FOOD),
    _cat("eggs", "Eggs", "Hen and specialty eggs", *_G_FOOD),
    _cat("frozen_food", "Frozen food", "Frozen meals, veggies, and ice cream", *_G_FOOD),
    _cat("organic_food", "Organic food", "Organic staples and produce", *_G_FOOD),
    # —— Beverages ——
    _cat("tea_coffee", "Tea & coffee", "Tea, coffee, and brew kits", *_G_BEV),
    _cat("soft_drinks", "Soft drinks", "Sodas and carbonated drinks", *_G_BEV),
    _cat("juices", "Juices & drinks", "Fruit juices, squashes, and health drinks", *_G_BEV),
    _cat("water", "Water", "Packaged drinking water", *_G_BEV),
    _cat("energy_sports_drinks", "Energy & sports drinks", "Energy and electrolyte drinks", *_G_BEV),
    # —— Health & Beauty ——
    _cat("personal_care", "Personal care", "Soap, shampoo, oral care, and hygiene", *_G_HEALTH),
    _cat("skincare", "Skincare", "Creams, lotions, and face care", *_G_HEALTH),
    _cat("haircare", "Haircare", "Hair oils, dyes, and treatments", *_G_HEALTH),
    _cat("cosmetics", "Cosmetics & makeup", "Makeup and beauty tools", *_G_HEALTH),
    _cat("fragrances", "Fragrances", "Perfumes and deodorants", *_G_HEALTH),
    _cat("otc_pharmacy", "OTC & pharmacy", "Over-the-counter medicines and first aid", *_G_HEALTH),
    _cat("wellness_supplements", "Wellness & supplements", "Vitamins, proteins, and nutraceuticals", *_G_HEALTH),
    _cat("ayurveda", "Ayurveda", "Ayurvedic products", *_G_HEALTH),
    # —— Baby & Kids Care ——
    _cat("baby_care", "Baby care", "Diapers, wipes, and baby toiletries", *_G_BABY),
    _cat("baby_food", "Baby food", "Formula, cereals, and toddler foods", *_G_BABY),
    _cat("kids_essentials", "Kids essentials", "Kids toiletries and daily care", *_G_BABY),
    # —— Home & Household ——
    _cat("cleaning", "Cleaning supplies", "Detergents, cleaners, and brooms", *_G_HOME),
    _cat("laundry", "Laundry", "Detergent, softener, and stain removers", *_G_HOME),
    _cat("kitchenware", "Kitchenware", "Utensils, cookware, and storage", *_G_HOME),
    _cat("home_decor", "Home décor", "Decor, frames, and furnishings", *_G_HOME),
    _cat("furniture", "Furniture", "Tables, chairs, and storage furniture", *_G_HOME),
    _cat("bedding_bath", "Bedding & bath", "Bedsheets, towels, and mattresses", *_G_HOME),
    _cat("hardware", "Hardware & tools", "Tools, fasteners, and DIY supplies", *_G_HOME),
    _cat("paint", "Paints & finishes", "Paints, primers, and brushes", *_G_HOME),
    _cat("electrical_supplies", "Electrical supplies", "Switches, bulbs, wires, and boards", *_G_HOME),
    _cat("sanitary", "Sanitary & plumbing", "Taps, pipes, and bathroom fittings", *_G_HOME),
    # —— Electronics ——
    _cat("mobile_phones", "Mobile phones", "Smartphones and feature phones", *_G_ELEC),
    _cat("mobile_accessories", "Mobile accessories", "Chargers, cases, earphones, and power banks", *_G_ELEC),
    _cat("computers_laptops", "Computers & laptops", "PCs, laptops, and tablets", *_G_ELEC),
    _cat("computer_accessories", "Computer accessories", "Keyboards, mice, storage, and cables", *_G_ELEC),
    _cat("audio", "Audio", "Speakers, headphones, and soundbars", *_G_ELEC),
    _cat("tv_video", "TV & video", "Televisions and media players", *_G_ELEC),
    _cat("cameras", "Cameras & photography", "Cameras and photography gear", *_G_ELEC),
    _cat("wearables", "Wearables", "Smartwatches and fitness bands", *_G_ELEC),
    _cat("gaming", "Gaming", "Consoles, games, and controllers", *_G_ELEC),
    # —— Appliances ——
    _cat("kitchen_appliances", "Kitchen appliances", "Mixers, induction, and small kitchen appliances", *_G_APPL),
    _cat("home_appliances", "Home appliances", "Fans, irons, and vacuum cleaners", *_G_APPL),
    _cat("large_appliances", "Large appliances", "Refrigerators, washing machines, and ACs", *_G_APPL),
    # —— Fashion ——
    _cat("mens_apparel", "Men's apparel", "Men's clothing and ethnic wear", *_G_FASHION),
    _cat("womens_apparel", "Women's apparel", "Women's clothing and ethnic wear", *_G_FASHION),
    _cat("kids_apparel", "Kids' apparel", "Children's and infant clothing", *_G_FASHION),
    _cat("innerwear", "Innerwear & loungewear", "Innerwear, socks, and sleepwear", *_G_FASHION),
    _cat("activewear", "Activewear", "Sports and athleisure apparel", *_G_FASHION),
    _cat("fabric_textiles", "Fabric & textiles", "Cloth, saree materials, and rolls", *_G_FASHION),
    # —— Footwear ——
    _cat("mens_footwear", "Men's footwear", "Men's shoes and sandals", *_G_FOOT),
    _cat("womens_footwear", "Women's footwear", "Women's shoes and sandals", *_G_FOOT),
    _cat("kids_footwear", "Kids' footwear", "Children's shoes", *_G_FOOT),
    _cat("sports_footwear", "Sports footwear", "Running and athletic shoes", *_G_FOOT),
    # —— Jewellery & Accessories ——
    _cat("jewellery_fashion", "Fashion jewellery", "Imitation and fashion jewellery", *_G_JEWEL),
    _cat("jewellery_precious", "Precious jewellery", "Gold, silver, and diamond jewellery", *_G_JEWEL),
    _cat("watches", "Watches", "Wristwatches and straps", *_G_JEWEL),
    _cat("bags_wallets", "Bags & wallets", "Handbags, backpacks, and wallets", *_G_JEWEL),
    _cat("luggage", "Luggage", "Suitcases and travel bags", *_G_JEWEL),
    _cat("eyewear", "Eyewear", "Spectacles and sunglasses", *_G_JEWEL),
    # —— Sports ——
    _cat("sports_equipment", "Sports equipment", "Balls, bats, and training gear", *_G_SPORTS),
    _cat("fitness", "Fitness", "Dumbbells, yoga, and gym accessories", *_G_SPORTS),
    _cat("outdoor_camping", "Outdoor & camping", "Camping and outdoor gear", *_G_SPORTS),
    # —— Stationery ——
    _cat("stationery", "Stationery", "Pens, notebooks, and desk supplies", *_G_STAT),
    _cat("office_supplies", "Office supplies", "Files, toner, and office consumables", *_G_STAT),
    _cat("art_craft", "Art & craft", "Art materials and craft kits", *_G_STAT),
    _cat("books_magazines", "Books & magazines", "Books, comics, and magazines", *_G_STAT),
    # —— Toys ——
    _cat("toys", "Toys", "Toys and playsets", *_G_TOYS),
    _cat("board_games", "Board games & puzzles", "Indoor games and puzzles", *_G_TOYS),
    _cat("educational_toys", "Educational toys", "Learning toys and STEM kits", *_G_TOYS),
    # —— Pet ——
    _cat("pet_food", "Pet food", "Dog, cat, and other pet food", *_G_PET),
    _cat("pet_accessories", "Pet accessories", "Leashes, bowls, and toys", *_G_PET),
    _cat("pet_care", "Pet care", "Grooming and pet health", *_G_PET),
    # —— Automotive ——
    _cat("car_accessories", "Car accessories", "Car care and interior accessories", *_G_AUTO),
    _cat("bike_accessories", "Bike accessories", "Two-wheeler parts and accessories", *_G_AUTO),
    _cat("lubricants", "Oils & lubricants", "Engine oils and lubricants", *_G_AUTO),
    # —— Agriculture ——
    _cat("seeds", "Seeds", "Crop and kitchen garden seeds", *_G_AGRI),
    _cat("fertilizers", "Fertilizers & soil", "Fertilizers and soil conditioners", *_G_AGRI),
    _cat("animal_feed", "Animal feed", "Cattle and poultry feed", *_G_AGRI),
    _cat("farm_tools", "Farm tools", "Hand tools and farm implements", *_G_AGRI),
    # —— Services ——
    _cat("services", "Services", "Service SKUs and add-ons", *_G_SVC),
    _cat("repairs", "Repairs & maintenance", "Repair labour and service charges", *_G_SVC),
    # —— Software & Digital ——
    _cat("web_application", "Web application", "Web apps, portals, and SaaS UIs", *_G_SOFT),
    _cat("mobile_app", "Mobile app", "iOS, Android, and cross-platform apps", *_G_SOFT),
    _cat("saas", "SaaS", "Subscription software and cloud tools", *_G_SOFT),
    _cat("api_services", "API & integrations", "APIs, webhooks, and system integrations", *_G_SOFT),
    _cat("devops", "DevOps & hosting", "CI/CD, cloud hosting, and infrastructure", *_G_SOFT),
    _cat("ui_ux", "UI / UX design", "Product design, wireframes, and prototypes", *_G_SOFT),
    _cat("it_services", "IT services", "Support, maintenance, and consulting", *_G_SOFT),
    _cat("digital_marketing", "Digital marketing", "SEO, ads, and growth services", *_G_SOFT),
    # —— General ——
    _cat("gifts", "Gifts & party", "Gifts, party supplies, and novelty", *_G_GEN),
    _cat("religious", "Religious & pooja", "Pooja items and festival supplies", *_G_GEN),
    _cat("other", "Other", "Custom or uncategorized product type", *_G_GEN),
]


def product_category_values() -> set[str]:
    return {row.value for row in PRODUCT_CATEGORIES}


# Legacy values kept for label resolution of older products.
LEGACY_CATEGORY_ALIASES: dict[str, str] = {
    "grocery": "grocery_packaged",
    "snacks": "snacks_namkeen",
    "beverages": "soft_drinks",
    "frozen": "frozen_food",
    "spices": "spices_masala",
    "oils": "oils_ghee",
    "rice_flour": "rice",
    "sweets": "sweets_mithai",
    "meat": "meat_seafood",
    "organic": "organic_food",
    "health": "otc_pharmacy",
    "household": "cleaning",
    "electrical": "electrical_supplies",
    "electronics": "mobile_accessories",
    "mobile": "mobile_accessories",
    "appliances": "kitchen_appliances",
    "fashion": "mens_apparel",
    "footwear": "mens_footwear",
    "jewellery": "jewellery_fashion",
    "bags": "bags_wallets",
    "pet": "pet_food",
    "books": "books_magazines",
    "sports": "sports_equipment",
}
