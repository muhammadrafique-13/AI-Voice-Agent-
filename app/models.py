"""Database schema for the patient registration system.

Two write paths reach this schema (the REST API and the voice webhook), so correctness
is enforced *in the database* as well as in Pydantic. CHECK constraints below are the
backstop: if a future caller bypasses the service layer, the row still cannot be wrong.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    String,
    Text,
    func,
)

from app.db import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Patient(Base):
    """Standard U.S. minimum demographic dataset for patient intake.

    `patient_id` is a string-typed UUID rather than a native Postgres UUID column so the
    exact same schema and the same constraints run on SQLite locally and Postgres in
    production - no dev/prod divergence to debug at 2am.
    """

    __tablename__ = "patients"

    patient_id = Column(String(36), primary_key=True, default=_new_uuid)

    # --- Required demographics ---------------------------------------------
    first_name = Column(String(50), nullable=False)
    last_name = Column(String(50), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    sex = Column(String(20), nullable=False)
    # 10 bare digits for U.S. numbers, or E.164 with a leading "+" for international.
    phone_number = Column(String(20), nullable=False)
    address_line_1 = Column(String(200), nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(2), nullable=False)
    zip_code = Column(String(10), nullable=False)  # 12345 or 12345-6789

    # --- Optional demographics ---------------------------------------------
    email = Column(String(254), nullable=True)
    address_line_2 = Column(String(100), nullable=True)
    insurance_provider = Column(String(120), nullable=True)
    insurance_member_id = Column(String(60), nullable=True)
    preferred_language = Column(String(60), nullable=False, default="English")
    emergency_contact_name = Column(String(120), nullable=True)
    emergency_contact_phone = Column(String(20), nullable=True)

    # --- Provenance ---------------------------------------------------------
    # Which telephony call created this row. Doubles as the idempotency key: an LLM
    # occasionally fires the same tool twice, and a duplicate patient is worse than a
    # swallowed retry.
    registration_call_id = Column(String(120), nullable=True)
    source = Column(String(16), nullable=False, default="api")  # api | voice | seed

    # --- Audit / lifecycle --------------------------------------------------
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Soft delete only: DELETE /patients/:id stamps this instead of removing the row.
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Duplicate detection queries by phone on every inbound call.
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
        Index("ix_patients_registration_call_id", "registration_call_id"),
        # Database-level backstops mirroring the Pydantic rules.
        CheckConstraint(
            "sex IN ('Male','Female','Other','Decline to Answer')", name="ck_patients_sex"
        ),
        # 10 for a bare U.S. number, up to 16 for "+" plus 15 E.164 digits.
        CheckConstraint(
            "length(phone_number) BETWEEN 8 AND 16", name="ck_patients_phone_len"
        ),
        CheckConstraint("length(state) = 2", name="ck_patients_state_len"),
        CheckConstraint(
            "length(zip_code) IN (5, 10)", name="ck_patients_zip_len"
        ),
        CheckConstraint("length(first_name) BETWEEN 1 AND 50", name="ck_patients_first_len"),
        CheckConstraint("length(last_name) BETWEEN 1 AND 50", name="ck_patients_last_len"),
        # Static lower bound only. The "not in the future" rule is deliberately NOT a
        # CHECK constraint: CURRENT_DATE is non-immutable, so Postgres re-evaluates it
        # during pg_dump/restore and a perfectly valid old row can fail to reload.
        # That rule lives in validators.parse_dob instead, which both write paths use.
        CheckConstraint("date_of_birth >= '1900-01-01'", name="ck_patients_dob_floor"),
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class RegistrationDraft(Base):
    """Partial registration captured mid-call, so a dropped line does not lose everything.

    Kept in a separate table holding a JSON blob rather than as an 'in_progress' patient
    row, precisely so the `patients` CHECK/NOT NULL constraints above stay strict. A
    draft is incomplete by definition; a patient never is.

    Promoted (and cleared) when the caller confirms and the record is written.
    """

    __tablename__ = "registration_drafts"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    call_id = Column(String(120), nullable=False, unique=True, index=True)
    caller_phone = Column(String(20), nullable=True)
    payload = Column(Text, nullable=False, default="{}")  # JSON of fields so far
    completed_patient_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class CallTranscript(Base):
    """Bonus: end-of-call transcript/summary/recording, linked to the patient if created.

    Deliberately NOT a foreign key - a call can end without a registration, and that
    record is exactly the one worth keeping for follow-up.
    """

    __tablename__ = "call_transcripts"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    call_id = Column(String(120), nullable=True, unique=True, index=True)
    patient_id = Column(String(36), nullable=True, index=True)
    caller_phone = Column(String(20), nullable=True)
    ended_reason = Column(String(80), nullable=True)
    recording_url = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    duration_seconds = Column(String(16), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
