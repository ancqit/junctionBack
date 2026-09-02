import os

from pymongo import MongoClient


MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://invalid:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "junction")

mongo_client = MongoClient(
    MONGODB_URL,
    maxPoolSize=int(os.getenv("MONGODB_MAX_POOL_SIZE", "100")),
    minPoolSize=int(os.getenv("MONGODB_MIN_POOL_SIZE", "5")),
    maxIdleTimeMS=60_000,
    serverSelectionTimeoutMS=10_000,
    retryWrites=True,
)
database = mongo_client[MONGODB_DATABASE]
items = database["items"]
products = database["products"]
employees = database["employees"]
orders = database["orders"]
shops = database["shops"]
role_keeper = database["role_keeper"]
users = database["users"]
otp_requests = database["otp_requests"]
catalog_otp_requests = database["catalog_otp_requests"]
digilocker_states = database["digilocker_states"]
plan_applications = database["plan_applications"]
cities = database["cities"]
localities = database["localities"]
notices = database["notices"]
sessions = database["sessions"]
product_buckets = database["product_buckets"]
shop_payments = database["shop_payments"]
blog_entries = database["blog_entries"]
blog_profiles = database["blog_profiles"]
blog_counters = database["blog_counters"]
blog_accounts = database["blog_accounts"]
