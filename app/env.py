from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    """Load local .env files and Render secret files before app modules read config."""
    load_dotenv()

    secrets_dir = Path("/etc/secrets")
    if secrets_dir.is_dir():
        for secret_file in sorted(secrets_dir.iterdir()):
            if secret_file.is_file():
                load_dotenv(secret_file, override=False)
