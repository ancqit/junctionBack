import os

from slowapi import Limiter
from slowapi.util import get_remote_address

RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "20/minute")
RATE_LIMIT_AI = os.getenv("RATE_LIMIT_AI", "30/hour")

limiter = Limiter(key_func=get_remote_address)
