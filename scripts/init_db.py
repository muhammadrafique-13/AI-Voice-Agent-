"""Create tables against whatever DATABASE_URL points at.

Schema creation also happens lazily on the first request, so this script exists mainly
to fail loudly and early when a connection string is wrong - before a reviewer calls.

    python -m scripts.init_db
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before importing app.config, which reads the environment at import time.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:  # python-dotenv is a dev-only dependency
    pass

from app.config import get_settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402

if __name__ == "__main__":
    settings = get_settings()
    print(f"target: {engine.url.render_as_string(hide_password=True)}")
    if settings.is_sqlite:
        print("WARNING: using the local SQLite fallback - DATABASE_URL is not set.")
    init_db()
    print("schema created / verified.")
