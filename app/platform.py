"""Platform provenance for Junction users.

Main surfaces:
  - junction_website — junctionFrontweb (owner/admin login)
  - junction_today — junction.today consumer / catalog
  - junction_blog — junction.blog

Stored on the user (or contact) document at creation so ops can see where
the identity was born. Additive — no DB wipe required for existing rows.
"""

from enum import Enum


class Platform(str, Enum):
    junction_website = "junction_website"
    junction_today = "junction_today"
    junction_blog = "junction_blog"


PLATFORM_LABELS = {
    Platform.junction_website: "junction.website",
    Platform.junction_today: "junction.today",
    Platform.junction_blog: "junction.blog",
}


def normalize_platform(value: str | None, *, default: Platform = Platform.junction_website) -> Platform:
    raw = (value or "").strip().lower().replace("-", "_").replace(".", "_")
    aliases = {
        "website": Platform.junction_website,
        "web": Platform.junction_website,
        "frontweb": Platform.junction_website,
        "junction_website": Platform.junction_website,
        "junctionwebsite": Platform.junction_website,
        "today": Platform.junction_today,
        "jtoday": Platform.junction_today,
        "junction_today": Platform.junction_today,
        "junctiontoday": Platform.junction_today,
        "blog": Platform.junction_blog,
        "junction_blog": Platform.junction_blog,
        "junctionblog": Platform.junction_blog,
    }
    if raw in aliases:
        return aliases[raw]
    try:
        return Platform(raw)
    except ValueError:
        return default
