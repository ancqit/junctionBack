from enum import Enum

from pydantic import BaseModel


class ShopTypeCategory(str, Enum):
    retail = "retail"
    food = "food"
    beverage = "beverage"
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
    group: str | None = None
    description: str


def _type(
    value: str,
    label: str,
    category: ShopTypeCategory,
    description: str,
    group: str | None = None,
) -> ShopTypeInfo:
    return ShopTypeInfo(value=value, label=label, category=category, group=group, description=description)


SHOP_TYPES: list[ShopTypeInfo] = [
    # ── Retail ──────────────────────────────────────────────────────────────
    _type("general_retail", "General Retail Store", ShopTypeCategory.retail, "Everyday items, household goods, and mixed retail products", "general"),
    _type("kirana_grocery", "Kirana / Grocery Store", ShopTypeCategory.retail, "Neighbourhood grocery, provisions, and daily essentials", "grocery"),
    _type("supermarket", "Supermarket / Hypermarket", ShopTypeCategory.retail, "Large-format self-service store with wide product range", "grocery"),
    _type("convenience_store", "Convenience Store", ShopTypeCategory.retail, "Small quick-stop shop for snacks and daily basics", "grocery"),
    _type("department_store", "Department Store", ShopTypeCategory.retail, "Multi-category retail under one roof", "general"),
    _type("mobile_retail", "Mobile & Accessories Retail", ShopTypeCategory.retail, "Smartphones, tablets, chargers, and mobile accessories", "electronics"),
    _type("electronics_retail", "Electronics Retail", ShopTypeCategory.retail, "TVs, audio, cameras, and consumer electronics", "electronics"),
    _type("computer_retail", "Computer & IT Retail", ShopTypeCategory.retail, "Laptops, desktops, peripherals, and IT accessories", "electronics"),
    _type("appliance_retail", "Home Appliance Retail", ShopTypeCategory.retail, "Refrigerators, washing machines, ACs, and kitchen appliances", "electronics"),
    _type("clothing_store", "Clothing Store", ShopTypeCategory.retail, "Apparel for men, women, or children", "fashion"),
    _type("mens_clothing", "Men's Clothing Store", ShopTypeCategory.retail, "Men's apparel, formal wear, and casual clothing", "fashion"),
    _type("womens_clothing", "Women's Clothing Store", ShopTypeCategory.retail, "Women's apparel, ethnic wear, and western wear", "fashion"),
    _type("kids_clothing", "Kids & Baby Clothing", ShopTypeCategory.retail, "Children's and infant apparel", "fashion"),
    _type("footwear_store", "Footwear Store", ShopTypeCategory.retail, "Shoes, sandals, sneakers, and footwear accessories", "fashion"),
    _type("jewelry_store", "Jewelry Store", ShopTypeCategory.retail, "Gold, silver, imitation, and fashion jewelry", "fashion"),
    _type("boutique", "Boutique / Fashion Boutique", ShopTypeCategory.retail, "Curated fashion, designer wear, or specialty clothing", "fashion"),
    _type("sportswear_store", "Sportswear & Sports Goods", ShopTypeCategory.retail, "Athletic apparel, shoes, and sports equipment", "fashion"),
    _type("hardware_store", "Hardware Store", ShopTypeCategory.retail, "Tools, fasteners, paints, and building supplies", "home"),
    _type("paint_store", "Paint Store", ShopTypeCategory.retail, "Paints, primers, brushes, and painting supplies", "home"),
    _type("sanitary_ware", "Sanitary Ware & Bathroom Fittings", ShopTypeCategory.retail, "Taps, basins, tiles, and bathroom fixtures", "home"),
    _type("stationery_store", "Stationery & Office Supplies", ShopTypeCategory.retail, "Pens, paper, and office stationery", "general"),
    _type("bookstore", "Bookstore", ShopTypeCategory.retail, "Books, magazines, and reading materials", "general"),
    _type("toy_store", "Toy & Kids Store", ShopTypeCategory.retail, "Toys, games, and children's products", "general"),
    _type("pet_store", "Pet Store", ShopTypeCategory.retail, "Pet food, accessories, and pet care products", "general"),
    _type("florist", "Florist / Flower Shop", ShopTypeCategory.retail, "Fresh flowers, bouquets, and floral arrangements", "general"),
    _type("gift_shop", "Gift Shop", ShopTypeCategory.retail, "Gifts, souvenirs, and novelty items", "general"),
    _type("medical_equipment_retail", "Medical Equipment Retail", ShopTypeCategory.retail, "Wheelchairs, monitors, and home medical devices", "health"),
    _type("religious_store", "Religious / Pooja Store", ShopTypeCategory.retail, "Idols, incense, pooja items, and festival supplies", "general"),
    _type("second_hand_store", "Second-Hand / Thrift Store", ShopTypeCategory.retail, "Pre-owned goods and resale retail", "general"),
    # ── Food ────────────────────────────────────────────────────────────────
    _type("restaurant", "Restaurant", ShopTypeCategory.food, "Dine-in or takeaway food service", "dining"),
    _type("fast_food", "Fast Food Outlet", ShopTypeCategory.food, "Quick-service burgers, wraps, fried food, and combos", "dining"),
    _type("cafe", "Café / Coffee Shop", ShopTypeCategory.food, "Coffee, tea, snacks, and light meals", "dining"),
    _type("bakery", "Bakery / Confectionery", ShopTypeCategory.food, "Bread, cakes, pastries, and baked goods", "fresh"),
    _type("sweet_shop", "Sweet Shop / Mithai Store", ShopTypeCategory.food, "Indian sweets, namkeen, and festive confectionery", "fresh"),
    _type("meat_seafood", "Meat & Seafood Shop", ShopTypeCategory.food, "Fresh or frozen meat, poultry, and seafood", "fresh"),
    _type("fruits_vegetables", "Fruits & Vegetables Store", ShopTypeCategory.food, "Fresh produce retail", "fresh"),
    _type("dairy_milk_booth", "Dairy / Milk Booth", ShopTypeCategory.food, "Milk, curd, paneer, and daily dairy products", "fresh"),
    _type("cloud_kitchen", "Cloud Kitchen", ShopTypeCategory.food, "Delivery-only kitchen with no dine-in", "dining"),
    _type("food_truck", "Food Truck / Street Food", ShopTypeCategory.food, "Mobile or street-side prepared food", "dining"),
    _type("catering", "Catering Service", ShopTypeCategory.food, "Event and party food preparation and delivery", "dining"),
    _type("ice_cream_parlour", "Ice Cream Parlour", ShopTypeCategory.food, "Ice cream, gelato, and frozen desserts", "dining"),
    _type("snacks_chaat", "Snacks / Chaat Stall", ShopTypeCategory.food, "Street snacks, chaat, and quick bites", "dining"),
    # ── Beverage ────────────────────────────────────────────────────────────
    _type("beverage_store", "Beverage Store", ShopTypeCategory.beverage, "Packaged drinks, juices, and beverage retail", "retail"),
    _type("juice_bar", "Juice Bar", ShopTypeCategory.beverage, "Fresh juices, smoothies, and shakes", "prepared"),
    _type("tea_stall", "Tea Stall / Chai Shop", ShopTypeCategory.beverage, "Tea, coffee, and light refreshments", "prepared"),
    _type("wine_liquor_store", "Wine & Liquor Store", ShopTypeCategory.beverage, "Alcoholic beverages where legally permitted", "retail"),
    _type("water_refill", "Water Refill / RO Plant", ShopTypeCategory.beverage, "Packaged or refilled drinking water", "retail"),
    # ── Home & living (retail) ──────────────────────────────────────────────
    _type("furniture_store", "Furniture Store", ShopTypeCategory.home_and_living, "Home and office furniture retail", "furniture"),
    _type("mattress_bedding", "Mattress & Bedding Store", ShopTypeCategory.home_and_living, "Mattresses, pillows, and bedding", "furniture"),
    _type("home_decor", "Home Décor & Furnishings", ShopTypeCategory.home_and_living, "Curtains, lighting, and decorative items", "decor"),
    _type("kitchenware_store", "Kitchenware & Cookware", ShopTypeCategory.home_and_living, "Utensils, cookware, and kitchen accessories", "kitchen"),
    _type("home_improvement", "Home Improvement Store", ShopTypeCategory.home_and_living, "Renovation, plumbing, electrical, and DIY supplies", "building"),
    _type("lighting_store", "Lighting Store", ShopTypeCategory.home_and_living, "LED lights, lamps, and fixtures", "decor"),
    _type("crockery_store", "Crockery & Tableware", ShopTypeCategory.home_and_living, "Plates, glasses, and dining sets", "kitchen"),
    # ── Health & beauty ─────────────────────────────────────────────────────
    _type("pharmacy", "Pharmacy / Medical Store", ShopTypeCategory.health_and_beauty, "Medicines, OTC health products, and wellness items", "health"),
    _type("ayurveda_herbal", "Ayurveda / Herbal Store", ShopTypeCategory.health_and_beauty, "Ayurvedic medicines and herbal products", "health"),
    _type("cosmetics_store", "Cosmetics & Beauty Products", ShopTypeCategory.health_and_beauty, "Makeup, skincare, and personal care products", "beauty"),
    _type("salon", "Salon / Beauty Parlour", ShopTypeCategory.health_and_beauty, "Hair, skin, and beauty services", "beauty"),
    _type("barber_shop", "Barber Shop / Men's Salon", ShopTypeCategory.health_and_beauty, "Men's haircuts, grooming, and beard care", "beauty"),
    _type("spa_wellness", "Spa & Wellness Centre", ShopTypeCategory.health_and_beauty, "Spa treatments, massage, and wellness services", "beauty"),
    _type("optical_store", "Optical Store", ShopTypeCategory.health_and_beauty, "Eyewear, lenses, and vision care products", "health"),
    _type("fitness_nutrition", "Fitness & Nutrition Store", ShopTypeCategory.health_and_beauty, "Protein supplements, vitamins, and health foods", "health"),
    # ── Automotive (retail & showrooms) ─────────────────────────────────────
    _type("auto_parts", "Auto Parts Store", ShopTypeCategory.automotive, "Spare parts and accessories for cars and bikes", "parts"),
    _type("bike_showroom", "Bike / Two-Wheeler Showroom", ShopTypeCategory.automotive, "Motorcycle and scooter sales", "showroom"),
    _type("car_showroom", "Car Showroom / Dealership", ShopTypeCategory.automotive, "New or used car sales", "showroom"),
    _type("fuel_station", "Fuel Station / Petrol Pump", ShopTypeCategory.automotive, "Petrol, diesel, CNG, and convenience retail", "fuel"),
    _type("tyre_store", "Tyre Store", ShopTypeCategory.automotive, "Tyres, wheels, and alignment services", "parts"),
    _type("battery_store", "Battery Store", ShopTypeCategory.automotive, "Vehicle and inverter batteries", "parts"),
    _type("ev_charging", "EV Charging Station", ShopTypeCategory.automotive, "Electric vehicle charging services", "fuel"),
    # ── Services — home maintenance & trades ────────────────────────────────
    _type("plumber", "Plumber", ShopTypeCategory.services, "Pipe fitting, leak repair, and plumbing installation", "home_maintenance"),
    _type("electrician", "Electrician", ShopTypeCategory.services, "Wiring, switchboard repair, and electrical installation", "home_maintenance"),
    _type("carpenter", "Carpenter / Woodworker", ShopTypeCategory.services, "Furniture making, wood repair, and carpentry", "home_maintenance"),
    _type("painter", "Painter / Wall Contractor", ShopTypeCategory.services, "Interior and exterior painting services", "home_maintenance"),
    _type("mason", "Mason / Construction Worker", ShopTypeCategory.services, "Brickwork, tiling, and basic construction labour", "home_maintenance"),
    _type("roofer", "Roofer / Waterproofing", ShopTypeCategory.services, "Roof repair, terrace waterproofing, and sealing", "home_maintenance"),
    _type("welder_fabricator", "Welder / Fabricator", ShopTypeCategory.services, "Metal gates, grills, and fabrication work", "home_maintenance"),
    _type("glass_glazier", "Glass & Glazier", ShopTypeCategory.services, "Window glass, mirrors, and aluminium work", "home_maintenance"),
    _type("flooring_tiling", "Flooring & Tiling Contractor", ShopTypeCategory.services, "Tile, marble, and wooden flooring installation", "home_maintenance"),
    _type("false_ceiling", "False Ceiling Contractor", ShopTypeCategory.services, "POP, gypsum, and ceiling installation", "home_maintenance"),
    _type("modular_kitchen", "Modular Kitchen Installer", ShopTypeCategory.services, "Kitchen cabinets and countertop fitting", "home_maintenance"),
    _type("interior_designer", "Interior Designer", ShopTypeCategory.services, "Home and office interior design services", "construction"),
    _type("architect", "Architect", ShopTypeCategory.services, "Building design and architectural consultancy", "construction"),
    _type("civil_contractor", "Civil Contractor / Builder", ShopTypeCategory.services, "Construction, renovation, and project contracting", "construction"),
    # ── Services — technicians & repair ─────────────────────────────────────
    _type("general_technician", "General Technician", ShopTypeCategory.services, "Multi-skill repair and maintenance technician", "technician"),
    _type("ac_technician", "AC Technician", ShopTypeCategory.services, "Air conditioner installation, gas refill, and servicing", "technician"),
    _type("refrigerator_technician", "Refrigerator Technician", ShopTypeCategory.services, "Fridge and freezer repair and maintenance", "technician"),
    _type("washing_machine_technician", "Washing Machine Technician", ShopTypeCategory.services, "Washing machine repair and servicing", "technician"),
    _type("tv_technician", "TV & Home Theatre Technician", ShopTypeCategory.services, "Television and audio system repair", "technician"),
    _type("microwave_technician", "Microwave / Oven Technician", ShopTypeCategory.services, "Microwave and oven repair services", "technician"),
    _type("water_purifier_technician", "Water Purifier Technician", ShopTypeCategory.services, "RO, UV purifier service and filter replacement", "technician"),
    _type("inverter_technician", "Inverter & UPS Technician", ShopTypeCategory.services, "Inverter, battery, and power backup servicing", "technician"),
    _type("cctv_technician", "CCTV & Security System Technician", ShopTypeCategory.services, "Camera installation, DVR setup, and troubleshooting", "technician"),
    _type("laptop_technician", "Laptop Technician", ShopTypeCategory.services, "Laptop hardware and software repair", "technician"),
    _type("mobile_technician", "Mobile Phone Technician", ShopTypeCategory.services, "Smartphone screen, battery, and board repair", "technician"),
    _type("computer_technician", "Computer / Desktop Technician", ShopTypeCategory.services, "PC assembly, repair, and data recovery", "technician"),
    _type("printer_technician", "Printer Technician", ShopTypeCategory.services, "Printer repair, cartridge refill, and servicing", "technician"),
    _type("elevator_technician", "Elevator / Lift Technician", ShopTypeCategory.services, "Lift maintenance and breakdown repair", "technician"),
    _type("solar_technician", "Solar Panel Technician", ShopTypeCategory.services, "Solar panel installation and maintenance", "technician"),
    _type("generator_technician", "Generator Technician", ShopTypeCategory.services, "Diesel generator repair and servicing", "technician"),
    _type("ac_repair", "AC & Appliance Repair (General)", ShopTypeCategory.services, "Combined AC and home appliance servicing", "technician"),
    _type("mobile_repair", "Mobile & Electronics Repair Shop", ShopTypeCategory.services, "Phone, tablet, and gadget repair storefront", "technician"),
    _type("computer_repair", "Computer Repair & IT Support", ShopTypeCategory.services, "Laptop, desktop, and network support services", "technician"),
    # ── Services — automotive ───────────────────────────────────────────────
    _type("auto_mechanic", "Auto Mechanic / Garage", ShopTypeCategory.services, "Vehicle servicing and mechanical repair", "automotive_service"),
    _type("bike_mechanic", "Bike Mechanic", ShopTypeCategory.services, "Two-wheeler servicing and repair", "automotive_service"),
    _type("car_wash", "Car Wash & Detailing", ShopTypeCategory.services, "Vehicle washing, polishing, and detailing", "automotive_service"),
    _type("denting_painting", "Denting & Painting (Auto Body)", ShopTypeCategory.services, "Vehicle body repair and paint work", "automotive_service"),
    _type("towing_service", "Towing & Roadside Assistance", ShopTypeCategory.services, "Breakdown towing and on-road help", "automotive_service"),
    # ── Services — personal & domestic ──────────────────────────────────────
    _type("tailor", "Tailor / Alteration Services", ShopTypeCategory.services, "Stitching, alterations, and custom tailoring", "personal"),
    _type("embroidery", "Embroidery & Zari Work", ShopTypeCategory.services, "Embroidery, hemming, and garment finishing", "personal"),
    _type("laundry_dry_clean", "Laundry / Dry Cleaning", ShopTypeCategory.services, "Washing, ironing, and dry-cleaning services", "personal"),
    _type("ironing_press", "Ironing / Press Service", ShopTypeCategory.services, "Clothes pressing and ironing at home or shop", "personal"),
    _type("beautician_home_service", "Home Beauty Services", ShopTypeCategory.services, "At-home salon, makeup, and grooming", "personal"),
    _type("mehndi_artist", "Mehndi Artist", ShopTypeCategory.services, "Bridal and festive mehndi application", "personal"),
    _type("babysitter_nanny", "Babysitter / Nanny", ShopTypeCategory.services, "Childcare and babysitting services", "personal"),
    _type("cook_home_service", "Home Cook / Tiffin Service", ShopTypeCategory.services, "Home cooking, meal prep, and tiffin delivery", "personal"),
    _type("domestic_help", "Domestic Help / Housemaid", ShopTypeCategory.services, "Household help and domestic assistance", "personal"),
    _type("elderly_care", "Elderly Care Attendant", ShopTypeCategory.services, "Senior care and companion services at home", "personal"),
    # ── Services — cleaning & facility ──────────────────────────────────────
    _type("cleaning_services", "Cleaning Services", ShopTypeCategory.services, "Home, office, and deep-cleaning services", "cleaning"),
    _type("sofa_carpet_cleaning", "Sofa & Carpet Cleaning", ShopTypeCategory.services, "Upholstery shampooing and carpet washing", "cleaning"),
    _type("tank_cleaning", "Water Tank Cleaning", ShopTypeCategory.services, "Overhead and underground tank cleaning", "cleaning"),
    _type("pest_control", "Pest Control Services", ShopTypeCategory.services, "Residential and commercial pest management", "cleaning"),
    _type("sanitization", "Sanitization & Disinfection", ShopTypeCategory.services, "Premises sanitization and fumigation", "cleaning"),
    _type("housekeeping", "Housekeeping Services", ShopTypeCategory.services, "Regular housekeeping for homes and offices", "cleaning"),
    _type("facility_management", "Facility Management", ShopTypeCategory.services, "Building maintenance and facility operations", "cleaning"),
    # ── Services — security & safety ──────────────────────────────────────
    _type("security_services", "Security Guard Services", ShopTypeCategory.services, "Manned guarding and security personnel", "security"),
    _type("cctv_installation", "CCTV Installation Service", ShopTypeCategory.services, "Security camera setup and monitoring solutions", "security"),
    _type("fire_safety", "Fire Safety & Extinguisher Service", ShopTypeCategory.services, "Fire equipment refill, audit, and compliance", "security"),
    _type("locksmith", "Locksmith", ShopTypeCategory.services, "Lock repair, key duplication, and safe opening", "security"),
    # ── Services — logistics & delivery ─────────────────────────────────────
    _type("moving_packers", "Packers & Movers", ShopTypeCategory.services, "Relocation, packing, and logistics services", "logistics"),
    _type("courier_service", "Courier & Parcel Service", ShopTypeCategory.services, "Local and domestic parcel pickup and delivery", "logistics"),
    _type("bike_delivery", "Bike Delivery / Rider Service", ShopTypeCategory.services, "Last-mile delivery and errand running", "logistics"),
    _type("truck_transport", "Truck / Tempo Transport", ShopTypeCategory.services, "Goods transport and loading services", "logistics"),
    # ── Services — events & media ───────────────────────────────────────────
    _type("event_services", "Event Planning & Management", ShopTypeCategory.services, "Weddings, parties, and corporate events", "events"),
    _type("wedding_decorator", "Wedding Decorator", ShopTypeCategory.services, "Stage, floral, and venue decoration", "events"),
    _type("dj_sound", "DJ & Sound System", ShopTypeCategory.services, "Event sound, lighting, and DJ services", "events"),
    _type("photography_studio", "Photography / Videography", ShopTypeCategory.services, "Photo shoots, events, and video production", "events"),
    _type("catering_events", "Event Catering", ShopTypeCategory.services, "Large-scale catering for functions and weddings", "events"),
    # ── Services — professional & business ──────────────────────────────────
    _type("accountant_ca", "Accountant / CA Services", ShopTypeCategory.services, "Bookkeeping, GST filing, and tax consultancy", "professional"),
    _type("lawyer_legal", "Lawyer / Legal Services", ShopTypeCategory.services, "Legal advice, documentation, and representation", "professional"),
    _type("insurance_agent", "Insurance Agent", ShopTypeCategory.services, "Life, health, and vehicle insurance brokerage", "professional"),
    _type("real_estate_agent", "Real Estate Agent / Broker", ShopTypeCategory.services, "Property sales, rentals, and brokerage", "professional"),
    _type("travel_agent", "Travel Agent", ShopTypeCategory.services, "Tickets, tours, visas, and travel bookings", "professional"),
    _type("printing_xerox", "Printing / Xerox Shop", ShopTypeCategory.services, "Photocopying, printing, binding, and lamination", "professional"),
    _type("notary_document", "Notary & Documentation", ShopTypeCategory.services, "Affidavits, attestations, and document work", "professional"),
    _type("digital_marketing", "Digital Marketing / Social Media", ShopTypeCategory.services, "Online ads, SEO, and social media management", "professional"),
    _type("web_developer", "Web Developer / IT Freelancer", ShopTypeCategory.services, "Websites, apps, and software development services", "professional"),
    _type("consultant", "Business Consultant", ShopTypeCategory.services, "General business and operations consulting", "professional"),
    # ── Services — wellness & fitness (at home) ─────────────────────────────
    _type("yoga_trainer", "Yoga / Fitness Trainer", ShopTypeCategory.services, "Personal training and yoga instruction", "wellness"),
    _type("physiotherapist", "Physiotherapist", ShopTypeCategory.services, "Physical therapy and rehabilitation services", "wellness"),
    _type("home_nurse", "Home Nurse / Nursing Care", ShopTypeCategory.services, "Nursing and post-operative care at home", "wellness"),
    _type("astrologer", "Astrologer / Pandit", ShopTypeCategory.services, "Astrology, puja, and religious ceremony services", "wellness"),
    # ── Services — miscellaneous ──────────────────────────────────────────────
    _type("pesticide_spraying", "Gardening & Landscaping", ShopTypeCategory.services, "Lawn care, plants, and landscape maintenance", "misc"),
    _type("scrap_dealer", "Scrap Dealer / Kabadiwala", ShopTypeCategory.services, "Scrap collection and recycling pickup", "misc"),
    _type("key_maker", "Key Maker / Duplicate Keys", ShopTypeCategory.services, "Key cutting and remote programming", "misc"),
    _type("shoe_repair", "Shoe Repair / Cobbler", ShopTypeCategory.services, "Footwear repair and polishing", "misc"),
    _type("watch_repair", "Watch Repair", ShopTypeCategory.services, "Watch battery replacement and repair", "misc"),
    _type("umbrella_repair", "Umbrella / Bag Repair", ShopTypeCategory.services, "Small goods repair and mending", "misc"),
    # ── Wholesale & agriculture ─────────────────────────────────────────────
    _type("wholesale_distributor", "Wholesale / Distributor", ShopTypeCategory.wholesale, "Bulk supply and distribution to retailers", "general"),
    _type("wholesale_grocery", "Wholesale Grocery", ShopTypeCategory.wholesale, "Bulk food grains, pulses, and provisions", "food"),
    _type("agri_inputs", "Agriculture Inputs Store", ShopTypeCategory.agriculture, "Seeds, fertilizers, pesticides, and farm supplies", "inputs"),
    _type("farm_produce", "Farm Produce Seller", ShopTypeCategory.agriculture, "Direct sale of farm-grown produce", "produce"),
    _type("dairy_farm", "Dairy Farm / Milk Supplier", ShopTypeCategory.agriculture, "Farm-fresh milk and dairy supply", "produce"),
    # ── Education ───────────────────────────────────────────────────────────
    _type("tutor_coaching", "Tutor / Coaching Centre", ShopTypeCategory.education, "Private tuition, coaching classes, and test prep", "academic"),
    _type("driving_school", "Driving School", ShopTypeCategory.education, "Driving lessons and license training", "skills"),
    _type("music_dance_class", "Music / Dance Class", ShopTypeCategory.education, "Music, dance, and performing arts training", "skills"),
    _type("computer_training", "Computer Training Institute", ShopTypeCategory.education, "IT courses, coding, and office software training", "skills"),
    _type("language_class", "Language Class", ShopTypeCategory.education, "English and foreign language coaching", "skills"),
    _type("preschool_daycare", "Preschool / Daycare", ShopTypeCategory.education, "Early childhood education and childcare centre", "academic"),
    # ── Entertainment ───────────────────────────────────────────────────────
    _type("gym_fitness", "Gym / Fitness Centre", ShopTypeCategory.entertainment, "Fitness training, gym membership, and workouts", "fitness"),
    _type("sports_coaching", "Sports Coaching Academy", ShopTypeCategory.entertainment, "Cricket, football, and sports training", "fitness"),
    _type("gaming_parlour", "Gaming Parlour / Arcade", ShopTypeCategory.entertainment, "Video games, arcade, and entertainment centre", "leisure"),
    _type("movie_theatre", "Movie Theatre / Cinema", ShopTypeCategory.entertainment, "Film screenings and entertainment venue", "leisure"),
    _type("play_zone", "Kids Play Zone", ShopTypeCategory.entertainment, "Indoor play area and party venue for children", "leisure"),
    # ── Other ───────────────────────────────────────────────────────────────
    _type("other", "Other", ShopTypeCategory.other, "Business type not listed above", None),
]
