import os


def load_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:4200",
        "http://localhost:4211",
        "http://localhost:4300",
        "https://junction-frontweb.vercel.app",
        "https://junction.today",
        "https://www.junction.today",
        "https://junction-blog.vercel.app",
        "https://junction.blog",
        "https://www.junction.blog",
    ]
