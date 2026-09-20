"""CORS allow-list for browser + Capacitor WebView clients.

Important: if ``CORS_ORIGINS`` is set on Render it used to *replace* the defaults,
which silently dropped junction.today / Capacitor hosts and looked like a
``/session`` CORS failure. Env origins are now **merged** with defaults.
"""

from __future__ import annotations

import os
import re

# Browser sites + Capacitor virtual hosts (androidScheme/iosScheme https + hostname).
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:4200",
    "http://localhost:4201",
    "http://localhost:4211",
    "http://localhost:4300",
    "http://localhost",
    "https://localhost",
    # Capacitor / Ionic when server.hostname is not set (or older shells).
    "capacitor://localhost",
    "ionic://localhost",
    "https://junction-frontweb.vercel.app",
    "https://junction.today",
    "https://www.junction.today",
    "https://junction-blog.vercel.app",
    "https://junction.blog",
    "https://www.junction.blog",
    "https://junction.monster",
    "https://www.junction.monster",
    "https://junction.website",
    "https://www.junction.website",
    "https://j-monster.vercel.app",
    "https://jmonster.vercel.app",
)

# Preview deploys: https://*.vercel.app
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"^https://([a-z0-9-]+\.)*(junction\.today|junction\.website|junction\.blog|junction\.monster|vercel\.app)$",
).strip()


def load_cors_origins() -> list[str]:
    """Defaults ∪ ``CORS_ORIGINS`` env (comma-separated). Env never drops built-ins."""
    origins: list[str] = list(DEFAULT_CORS_ORIGINS)
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        for part in raw.split(","):
            origin = part.strip()
            if origin and origin not in origins:
                origins.append(origin)
    return origins


def load_cors_origin_regex() -> str | None:
    """Optional regex for Vercel previews / subdomains. Empty env disables it."""
    if not CORS_ORIGIN_REGEX:
        return None
    try:
        re.compile(CORS_ORIGIN_REGEX)
    except re.error:
        return None
    return CORS_ORIGIN_REGEX
