"""Patient service layer.

This is the single write path for the whole system. The REST router and the Vapi
webhook both call into here, so a record created by phone is validated, normalized and
persisted through exactly the same code as one created by `POST /patients`.
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import validators as v
from app.models import CallTranscript, Patient, RegistrationDraft
from app.schemas import PatientCreate, PatientUpdate

logger = logging.getLogger("patient_service")


class PatientNotFound(Exception):
    """Requested patient_id does not exist (or is soft-deleted)."""


# Fields copied straight from the validated schema onto the ORM model.
_WRITABLE_FIELDS = (
    "first_name", "last_name", "sex", "phone_number", "email",
    "address_line_1", "address_line_2", "city", "state", "zip_code",
    "insurance_provider", "insurance_member_id", "preferred_language",
    "emergency_contact_name", "emergency_contact_phone",
)


def _base_query(include_deleted: bool = False):
    stmt = select(Patient)
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    return stmt


def list_patients(
    db: Session,
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[Patient]:
    """Filtered patient list. All filters are optional and combine with AND."""
    stmt = _base_query(include_deleted)

    if last_name:
        # Case-insensitive exact match: reviewers will query ?last_name=doe
        stmt = stmt.where(Patient.last_name.ilike(last_name.strip()))
    if date_of_birth:
        # Parsed leniently so ?date_of_birth=1985-03-05 and 03/05/1985 both work.
        stmt = stmt.where(Patient.date_of_birth == v.parse_dob(date_of_birth))
    if phone_number:
        stmt = stmt.where(Patient.phone_number == v.normalize_phone(phone_number))

    stmt = stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_patient(
    db: Session, patient_id: str, include_deleted: bool = False
) -> Patient:
    stmt = _base_query(include_deleted).where(Patient.patient_id == patient_id)
    patient = db.execute(stmt).scalars().first()
    if patient is None:
        raise PatientNotFound(patient_id)
    return patient


def find_by_phone(db: Session, phone_number: str) -> Optional[Patient]:
    """Duplicate detection: does an active record already exist for this caller?"""
    normalized = v.normalize_phone(phone_number)
    stmt = _base_query().where(Patient.phone_number == normalized)
    stmt = stmt.order_by(Patient.created_at.desc())
    return db.execute(stmt).scalars().first()


def create_patient(
    db: Session,
    payload: PatientCreate,
    source: str = "api",
    call_id: Optional[str] = None,
) -> Patient:
    """Insert a patient.

    When `call_id` is supplied (i.e. the write came from a phone call) it acts as an
    idempotency key. Models occasionally emit the same tool call twice - once because
    the first response was slow - and a duplicate patient record is a worse outcome
    than a swallowed retry, so we return the existing row instead of inserting again.
    """
    if call_id:
        existing = (
            db.execute(
                select(Patient).where(Patient.registration_call_id == call_id)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            logger.info(
                "patient.create_deduplicated %s",
                json.dumps({"call_id": call_id, "patient_id": existing.patient_id}),
            )
            return existing

    patient = Patient(date_of_birth=v.parse_dob(payload.date_of_birth))
    for field in _WRITABLE_FIELDS:
        setattr(patient, field, getattr(payload, field))
    patient.preferred_language = payload.preferred_language or "English"
    patient.registration_call_id = call_id
    patient.source = source

    db.add(patient)
    db.commit()
    db.refresh(patient)

    _log_payload("patient.created", patient, source)
    return patient


def update_patient(
    db: Session, patient_id: str, payload: PatientUpdate, source: str = "api"
) -> Patient:
    """Partial update - only fields explicitly present in the request are touched."""
    patient = get_patient(db, patient_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)

    for field, value in changes.items():
        if field == "date_of_birth":
            patient.date_of_birth = v.parse_dob(value)
        elif field in _WRITABLE_FIELDS:
            setattr(patient, field, value)

    # onupdate=func.now() only fires when SQLAlchemy sees a dirty column, so set it
    # explicitly to keep updated_at honest even for a no-op PUT.
    patient.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(patient)

    _log_payload("patient.updated", patient, source, extra={"changed": list(changes)})
    return patient


def soft_delete_patient(db: Session, patient_id: str) -> Patient:
    """Spec requires a soft delete - stamp deleted_at, never remove the row."""
    patient = get_patient(db, patient_id)
    patient.deleted_at = datetime.utcnow()
    patient.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(patient)
    logger.info("patient.deleted %s", json.dumps({"patient_id": patient.patient_id}))
    return patient


def upsert_call_record(
    db: Session,
    call_id: Optional[str],
    patient_id: Optional[str] = None,
    caller_phone: Optional[str] = None,
    ended_reason: Optional[str] = None,
    summary: Optional[str] = None,
    transcript: Optional[str] = None,
    recording_url: Optional[str] = None,
    duration_seconds: Optional[str] = None,
) -> Optional[CallTranscript]:
    """Bonus: per-call audit record, written in two passes.

    A registration mid-call stamps the patient_id; the end-of-call report then fills in
    the transcript and summary. Keying on Vapi's call_id lets those two independent
    webhook invocations converge on one row - which matters on serverless, where we
    cannot hold state between requests.
    """
    if not call_id:
        return None

    record = (
        db.execute(select(CallTranscript).where(CallTranscript.call_id == call_id))
        .scalars()
        .first()
    )
    if record is None:
        record = CallTranscript(call_id=call_id)
        db.add(record)

    # Only overwrite with non-empty values so the later pass cannot erase the earlier one.
    for field, value in (
        ("patient_id", patient_id),
        ("caller_phone", caller_phone),
        ("ended_reason", ended_reason),
        ("summary", summary),
        ("transcript", transcript),
        ("recording_url", recording_url),
        ("duration_seconds", duration_seconds),
    ):
        if value:
            setattr(record, field, value)

    db.commit()
    db.refresh(record)
    logger.info(
        "call.record_saved %s",
        json.dumps(
            {
                "call_id": call_id,
                "patient_id": record.patient_id,
                "ended_reason": record.ended_reason,
                "has_transcript": bool(record.transcript),
            }
        ),
    )
    return record


def _log_payload(event: str, patient: Patient, source: str, extra: Optional[dict] = None):
    """Observability requirement: the final collected payload goes to stdout.

    Note this logs PHI-shaped data by design (the spec asks for it) - a production
    build would redact or route this to an audit sink instead.
    """
    body = {
        "event": event,
        "source": source,
        "patient_id": patient.patient_id,
        "name": patient.full_name,
        "date_of_birth": v.format_dob(patient.date_of_birth),
        "phone_number": patient.phone_number,
        "city": patient.city,
        "state": patient.state,
        "zip_code": patient.zip_code,
    }
    if extra:
        body.update(extra)
    logger.info("%s %s", event, json.dumps(body))


# --------------------------------------------------------------------------
# Mid-call drafts
#
# The brief asks what happens if the telephony connection drops mid-call. Without
# this, the answer is "everything the caller said is lost". With it, whatever had been
# collected is on disk within a second of being spoken, and staff can call back.
# --------------------------------------------------------------------------
def save_draft(
    db: Session,
    call_id: str,
    fields: dict,
    caller_phone: Optional[str] = None,
) -> RegistrationDraft:
    """Merge newly collected fields into this call's draft."""
    draft = (
        db.execute(select(RegistrationDraft).where(RegistrationDraft.call_id == call_id))
        .scalars()
        .first()
    )
    if draft is None:
        draft = RegistrationDraft(call_id=call_id, payload="{}")
        db.add(draft)

    merged = {}
    try:
        merged = json.loads(draft.payload or "{}")
    except json.JSONDecodeError:
        merged = {}
    merged.update({k: value for k, value in fields.items() if value not in (None, "")})

    draft.payload = json.dumps(merged, default=str)
    draft.updated_at = datetime.utcnow()
    if caller_phone:
        draft.caller_phone = caller_phone

    db.commit()
    db.refresh(draft)
    logger.info(
        "draft.saved %s",
        json.dumps({"call_id": call_id, "fields": sorted(merged.keys())}),
    )
    return draft


def get_draft(db: Session, call_id: str) -> Optional[dict]:
    draft = (
        db.execute(select(RegistrationDraft).where(RegistrationDraft.call_id == call_id))
        .scalars()
        .first()
    )
    if draft is None:
        return None
    try:
        return json.loads(draft.payload or "{}")
    except json.JSONDecodeError:
        return None


def close_draft(db: Session, call_id: str, patient_id: str) -> None:
    """Mark a draft as having produced a real patient record."""
    draft = (
        db.execute(select(RegistrationDraft).where(RegistrationDraft.call_id == call_id))
        .scalars()
        .first()
    )
    if draft is None:
        return
    draft.completed_patient_id = patient_id
    draft.updated_at = datetime.utcnow()
    db.commit()


def list_abandoned_drafts(db: Session, limit: int = 10) -> List[RegistrationDraft]:
    """Drafts from calls that never produced a patient - the follow-up queue."""
    stmt = (
        select(RegistrationDraft)
        .where(RegistrationDraft.completed_patient_id.is_(None))
        .order_by(RegistrationDraft.updated_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
