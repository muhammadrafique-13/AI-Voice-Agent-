"""Create the schema and insert demo patients.

Idempotent - safe to run repeatedly against the same database. Run it once after
provisioning Postgres so the dashboard is not empty when the reviewer opens it:

    python -m scripts.seed
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

from app.db import init_db, session_scope  # noqa: E402
from app.schemas import PatientCreate  # noqa: E402
from app.services import patients as service  # noqa: E402

SEED_PATIENTS = [
    PatientCreate(
        first_name="Jane",
        last_name="Doe",
        date_of_birth="03/05/1985",
        sex="Female",
        phone_number="5125550142",
        email="jane.doe@example.com",
        address_line_1="412 Oak Street",
        address_line_2="Apt 3B",
        city="Austin",
        state="TX",
        zip_code="78704",
        insurance_provider="Blue Cross Blue Shield",
        insurance_member_id="BCBS8842190",
        preferred_language="English",
        emergency_contact_name="Michael Doe",
        emergency_contact_phone="5125550188",
    ),
    PatientCreate(
        first_name="Luis",
        last_name="Ramirez",
        date_of_birth="11/22/1971",
        sex="Male",
        phone_number="9735550119",
        address_line_1="88 Clyde Road",
        city="Somerset",
        state="NJ",
        zip_code="08873",
        preferred_language="Spanish",
    ),
]


def main() -> None:
    init_db()
    created, skipped = 0, 0
    with session_scope() as db:
        for payload in SEED_PATIENTS:
            if service.find_by_phone(db, payload.phone_number):
                skipped += 1
                print(f"  skip   {payload.first_name} {payload.last_name} (already present)")
                continue
            patient = service.create_patient(db, payload, source="seed")
            created += 1
            print(f"  create {patient.full_name}  {patient.patient_id}")
    print(f"\nSeed complete: {created} created, {skipped} already present.")


if __name__ == "__main__":
    main()
