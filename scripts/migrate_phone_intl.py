"""Widen the phone columns so international (E.164) numbers fit.

The first schema stored U.S. numbers as exactly 10 digits, with a CHECK enforcing that
length. Supporting callers outside the U.S. means storing "+" plus up to 15 digits, so
the columns and the constraint both have to move.

`create_all` only creates missing tables - it never alters an existing one - so this
runs as an explicit, idempotent migration:

    python -m scripts.migrate_phone_intl

Postgres only. A local SQLite database is disposable: delete patients.db and re-run
scripts.init_db instead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine  # noqa: E402

STATEMENTS = (
    "ALTER TABLE patients ALTER COLUMN phone_number TYPE varchar(20)",
    "ALTER TABLE patients ALTER COLUMN emergency_contact_phone TYPE varchar(20)",
    "ALTER TABLE patients DROP CONSTRAINT IF EXISTS ck_patients_phone_len",
    "ALTER TABLE patients ADD CONSTRAINT ck_patients_phone_len "
    "CHECK (length(phone_number) BETWEEN 8 AND 16)",
)


def main() -> None:
    settings = get_settings()
    if settings.is_sqlite:
        raise SystemExit(
            "SQLite detected. Delete patients.db and run scripts.init_db instead."
        )

    print(f"target: {engine.url.render_as_string(hide_password=True)}")
    with engine.begin() as conn:
        for sql in STATEMENTS:
            conn.execute(text(sql))
            print("  ok:", sql[:78])

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='patients' AND column_name='phone_number'"
        )).scalar()
        print(f"\nphone_number is now varchar({row}).")


if __name__ == "__main__":
    main()
