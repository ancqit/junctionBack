from enum import Enum

from pydantic import BaseModel


class ShopTypeCategory(str, Enum):
    retail = "retail"
    food_and_beverage = "food_and_beverage"
    health_and_beauty = "health_and_beauty"
    home_and_living = "home_and_living"
    automotive = "automotive"
    services = "services"
    wholesale = "wholesale"
    agriculture = "agriculture"
    education = "education"
    entertainment = "entertainment"
    other = "other"


class ShopTypeInfo(BaseModel):
    value: str
    label: str
    category: ShopTypeCategory
    description: str


SHOP_TYPES: list[ShopTypeInfo] = [
    # Retail — general & grocery
    ShopTypeInfo(
        value="general_retail",
        label="General Retail Store",
        category=ShopTypeCategory.retail,
        description="Everyday items, household goods, and mixed retail products",
    ),
    ShopTypeInfo(
        value="kirana_grocery",
        label="Kirana / Grocery Store",
        category=ShopTypeCategory.retail,
        description="Neighbourhood grocery, provisions, and daily essentials",
    ),
    ShopTypeInfo(
        value="supermarket",
        label="Supermarket / Hypermarket",
        category=ShopTypeCategory.retail,
        description="Large-format self-service store with wide product range",
    ),
    ShopTypeInfo(
        value="convenience_store",
        label="Convenience Store",
        category=ShopTypeCategory.retail,
        description="Small quick-stop shop for snacks, beverages, and basics",
    ),
    ShopTypeInfo(
        value="department_store",
        label="Department Store",
        category=ShopTypeCategory.retail,
        description="Multi-category retail under one roof",
    ),
    # Retail — electronics & mobile
    ShopTypeInfo(
        value="mobile_retail",
        label="Mobile & Accessories Retail",
        category=ShopTypeCategory.retail,
        description="Smartphones, tablets, chargers, and mobile accessories",
    ),
    ShopTypeInfo(
        value="electronics_retail",
        label="Electronics Retail",
        category=ShopTypeCategory.retail,
        description="TVs, audio, cameras, and consumer electronics",
    ),
    ShopTypeInfo(
        value="computer_retail",
        label="Computer & IT Retail",
        category=ShopTypeCategory.retail,
        description="Laptops, desktops, peripherals, and IT accessories",
    ),
    ShopTypeInfo(
        value="appliance_retail",
        label="Home Appliance Retail",
        category=ShopTypeCategory.retail,
        description="Refrigerators, washing machines, ACs, and kitchen appliances",
    ),
    # Retail — fashion
    ShopTypeInfo(
        value="clothing_store",
        label="Clothing Store",
        category=ShopTypeCategory.retail,
        description="Apparel for men, women, or children",
    ),
    ShopTypeInfo(
        value="footwear_store",
        label="Footwear Store",
        category=ShopTypeCategory.retail,
        description="Shoes, sandals, sneakers, and footwear accessories",
    ),
    ShopTypeInfo(
        value="jewelry_store",
        label="Jewelry Store",
        category=ShopTypeCategory.retail,
        description="Gold, silver, imitation, and fashion jewelry",
    ),
    ShopTypeInfo(
        value="boutique",
        label="Boutique / Fashion Boutique",
        category=ShopTypeCategory.retail,
        description="Curated fashion, designer wear, or specialty clothing",
    ),
    ShopTypeInfo(
        value="sportswear_store",
        label="Sportswear & Sports Goods",
        category=ShopTypeCategory.retail,
        description="Athletic apparel, shoes, and sports equipment retail",
    ),
    # Retail — home & hardware
    ShopTypeInfo(
        value="hardware_store",
        label="Hardware Store",
        category=ShopTypeCategory.retail,
        description="Tools, fasteners, paints, and building supplies",
    ),
    ShopTypeInfo(
        value="home_improvement",
        label="Home Improvement Store",
        category=ShopTypeCategory.retail,
        description="Renovation, plumbing, electrical, and DIY supplies",
    ),
    ShopTypeInfo(
        value="furniture_store",
        label="Furniture Store",
        category=ShopTypeCategory.retail,
        description="Home and office furniture retail",
    ),
    ShopTypeInfo(
        value="home_decor",
        label="Home Décor & Furnishings",
        category=ShopTypeCategory.retail,
        description="Curtains, bedding, lighting, and decorative items",
    ),
    ShopTypeInfo(
        value="kitchenware_store",
        label="Kitchenware & Cookware",
        category=ShopTypeCategory.retail,
        description="Utensils, cookware, and kitchen accessories",
    ),
    # Food & beverage
    ShopTypeInfo(
        value="restaurant",
        label="Restaurant",
        category=ShopTypeCategory.food_and_beverage,
        description="Dine-in or takeaway food service",
    ),
    ShopTypeInfo(
        value="cafe",
        label="Café / Coffee Shop",
        category=ShopTypeCategory.food_and_beverage,
        description="Coffee, tea, snacks, and light meals",
    ),
    ShopTypeInfo(
        value="bakery",
        label="Bakery / Confectionery",
        category=ShopTypeCategory.food_and_beverage,
        description="Bread, cakes, pastries, and sweets",
    ),
    ShopTypeInfo(
        value="sweet_shop",
        label="Sweet Shop / Mithai Store",
        category=ShopTypeCategory.food_and_beverage,
        description="Indian sweets, namkeen, and festive confectionery",
    ),
    ShopTypeInfo(
        value="meat_seafood",
        label="Meat & Seafood Shop",
        category=ShopTypeCategory.food_and_beverage,
        description="Fresh or frozen meat, poultry, and seafood",
    ),
    ShopTypeInfo(
        value="fruits_vegetables",
        label="Fruits & Vegetables Store",
        category=ShopTypeCategory.food_and_beverage,
        description="Fresh produce retail",
    ),
    ShopTypeInfo(
        value="beverage_store",
        label="Beverage Store",
        category=ShopTypeCategory.food_and_beverage,
        description="Packaged drinks, juices, and beverage retail",
    ),
    # Health & beauty
    ShopTypeInfo(
        value="pharmacy",
        label="Pharmacy / Medical Store",
        category=ShopTypeCategory.health_and_beauty,
        description="Medicines, OTC health products, and wellness items",
    ),
    ShopTypeInfo(
        value="cosmetics_store",
        label="Cosmetics & Beauty Products",
        category=ShopTypeCategory.health_and_beauty,
        description="Makeup, skincare, and personal care products",
    ),
    ShopTypeInfo(
        value="salon",
        label="Salon / Beauty Parlour",
        category=ShopTypeCategory.health_and_beauty,
        description="Hair, skin, and beauty services",
    ),
    ShopTypeInfo(
        value="spa_wellness",
        label="Spa & Wellness Centre",
        category=ShopTypeCategory.health_and_beauty,
        description="Spa treatments, massage, and wellness services",
    ),
    ShopTypeInfo(
        value="optical_store",
        label="Optical Store",
        category=ShopTypeCategory.health_and_beauty,
        description="Eyewear, lenses, and vision care products",
    ),
    # Automotive
    ShopTypeInfo(
        value="auto_parts",
        label="Auto Parts Store",
        category=ShopTypeCategory.automotive,
        description="Spare parts and accessories for cars and bikes",
    ),
    ShopTypeInfo(
        value="bike_showroom",
        label="Bike / Two-Wheeler Showroom",
        category=ShopTypeCategory.automotive,
        description="Motorcycle and scooter sales",
    ),
    ShopTypeInfo(
        value="car_showroom",
        label="Car Showroom / Dealership",
        category=ShopTypeCategory.automotive,
        description="New or used car sales",
    ),
    ShopTypeInfo(
        value="fuel_station",
        label="Fuel Station / Petrol Pump",
        category=ShopTypeCategory.automotive,
        description="Petrol, diesel, CNG, and related convenience retail",
    ),
    ShopTypeInfo(
        value="tyre_store",
        label="Tyre Store",
        category=ShopTypeCategory.automotive,
        description="Tyres, wheels, and wheel alignment services",
    ),
    # Services — home & repair
    ShopTypeInfo(
        value="plumber",
        label="Plumber",
        category=ShopTypeCategory.services,
        description="Plumbing installation, repair, and maintenance services",
    ),
    ShopTypeInfo(
        value="electrician",
        label="Electrician",
        category=ShopTypeCategory.services,
        description="Electrical wiring, repair, and installation services",
    ),
    ShopTypeInfo(
        value="carpenter",
        label="Carpenter / Woodworker",
        category=ShopTypeCategory.services,
        description="Furniture making, wood repair, and carpentry services",
    ),
    ShopTypeInfo(
        value="painter",
        label="Painter / Contractor",
        category=ShopTypeCategory.services,
        description="Interior and exterior painting services",
    ),
    ShopTypeInfo(
        value="ac_repair",
        label="AC & Appliance Repair",
        category=ShopTypeCategory.services,
        description="Air conditioner and home appliance servicing",
    ),
    ShopTypeInfo(
        value="mobile_repair",
        label="Mobile & Electronics Repair",
        category=ShopTypeCategory.services,
        description="Phone, tablet, and gadget repair services",
    ),
    ShopTypeInfo(
        value="computer_repair",
        label="Computer Repair & IT Support",
        category=ShopTypeCategory.services,
        description="Laptop, desktop, and network support services",
    ),
    ShopTypeInfo(
        value="tailor",
        label="Tailor / Alteration Services",
        category=ShopTypeCategory.services,
        description="Stitching, alterations, and custom tailoring",
    ),
    ShopTypeInfo(
        value="laundry_dry_clean",
        label="Laundry / Dry Cleaning",
        category=ShopTypeCategory.services,
        description="Washing, ironing, and dry-cleaning services",
    ),
    ShopTypeInfo(
        value="cleaning_services",
        label="Cleaning Services",
        category=ShopTypeCategory.services,
        description="Home, office, and deep-cleaning services",
    ),
    ShopTypeInfo(
        value="pest_control",
        label="Pest Control Services",
        category=ShopTypeCategory.services,
        description="Residential and commercial pest management",
    ),
    ShopTypeInfo(
        value="security_services",
        label="Security Services",
        category=ShopTypeCategory.services,
        description="Guards, CCTV installation, and security solutions",
    ),
    ShopTypeInfo(
        value="moving_packers",
        label="Packers & Movers",
        category=ShopTypeCategory.services,
        description="Relocation, packing, and logistics services",
    ),
    ShopTypeInfo(
        value="auto_mechanic",
        label="Auto Mechanic / Garage",
        category=ShopTypeCategory.services,
        description="Vehicle servicing and mechanical repair",
    ),
    ShopTypeInfo(
        value="beautician_home_service",
        label="Home Beauty Services",
        category=ShopTypeCategory.services,
        description="At-home salon, makeup, and grooming services",
    ),
    ShopTypeInfo(
        value="tutor_coaching",
        label="Tutor / Coaching Centre",
        category=ShopTypeCategory.education,
        description="Private tuition, coaching classes, and test prep",
    ),
    ShopTypeInfo(
        value="driving_school",
        label="Driving School",
        category=ShopTypeCategory.education,
        description="Driving lessons and license training",
    ),
    ShopTypeInfo(
        value="stationery_store",
        label="Stationery & Office Supplies",
        category=ShopTypeCategory.retail,
        description="Pens, paper, books, and office stationery",
    ),
    ShopTypeInfo(
        value="bookstore",
        label="Bookstore",
        category=ShopTypeCategory.retail,
        description="Books, magazines, and reading materials",
    ),
    ShopTypeInfo(
        value="toy_store",
        label="Toy & Kids Store",
        category=ShopTypeCategory.retail,
        description="Toys, games, and children's products",
    ),
    ShopTypeInfo(
        value="pet_store",
        label="Pet Store",
        category=ShopTypeCategory.retail,
        description="Pet food, accessories, and pet care products",
    ),
    ShopTypeInfo(
        value="florist",
        label="Florist / Flower Shop",
        category=ShopTypeCategory.retail,
        description="Fresh flowers, bouquets, and floral arrangements",
    ),
    ShopTypeInfo(
        value="gift_shop",
        label="Gift Shop",
        category=ShopTypeCategory.retail,
        description="Gifts, souvenirs, and novelty items",
    ),
    ShopTypeInfo(
        value="wholesale_distributor",
        label="Wholesale / Distributor",
        category=ShopTypeCategory.wholesale,
        description="Bulk supply and distribution to retailers",
    ),
    ShopTypeInfo(
        value="agri_inputs",
        label="Agriculture Inputs Store",
        category=ShopTypeCategory.agriculture,
        description="Seeds, fertilizers, pesticides, and farm supplies",
    ),
    ShopTypeInfo(
        value="dairy_milk_booth",
        label="Dairy / Milk Booth",
        category=ShopTypeCategory.food_and_beverage,
        description="Milk, dairy products, and daily dairy retail",
    ),
    ShopTypeInfo(
        value="gym_fitness",
        label="Gym / Fitness Centre",
        category=ShopTypeCategory.entertainment,
        description="Fitness training, gym membership, and workouts",
    ),
    ShopTypeInfo(
        value="event_services",
        label="Event Planning & Services",
        category=ShopTypeCategory.services,
        description="Weddings, parties, décor, and event management",
    ),
    ShopTypeInfo(
        value="photography_studio",
        label="Photography / Videography Studio",
        category=ShopTypeCategory.services,
        description="Photo shoots, events, and video production services",
    ),
    ShopTypeInfo(
        value="travel_agency",
        label="Travel Agency",
        category=ShopTypeCategory.services,
        description="Tickets, tours, visas, and travel bookings",
    ),
    ShopTypeInfo(
        value="real_estate_agency",
        label="Real Estate Agency",
        category=ShopTypeCategory.services,
        description="Property sales, rentals, and brokerage",
    ),
    ShopTypeInfo(
        value="printing_xerox",
        label="Printing / Xerox Shop",
        category=ShopTypeCategory.services,
        description="Photocopying, printing, binding, and document services",
    ),
    ShopTypeInfo(
        value="other",
        label="Other",
        category=ShopTypeCategory.other,
        description="Business type not listed above",
    ),
]
