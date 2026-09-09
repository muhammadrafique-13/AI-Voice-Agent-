"""Pydantic request/response models.

These are the server-side validation layer. The voice agent does its own conversational
validation, but per the spec we never trust it - every field is re-validated here before
it reaches the database, whether it arrived via the REST API or the phone call.
"""
from datetime import date, datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import validators as v

T = TypeVar("T")


# --------------------------------------------------------------------------
# Response envelope: every response is {"data": ..., "error": ...}
# --------------------------------------------------------------------------
class ErrorDetail(BaseModel):
    code: str
    message: str
    fields: Optional[Dict[str, str]] = None


class Envelope(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None


# --------------------------------------------------------------------------
# Write models
# --------------------------------------------------------------------------
class PatientCreate(BaseModel):
    """Payload for POST /patients and for the agent's `register_patient` tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    first_name: str
    last_name: str
    date_of_birth: str = Field(description="MM/DD/YYYY (other common formats accepted)")
    sex: str
    phone_number: str
    address_line_1: str
    city: str
    state: str
    zip_code: str

    email: Optional[str] = None
    address_line_2: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    # --- Required-field normalizers ---------------------------------------
    @field_validator("first_name")
    @classmethod
    def _v_first(cls, value):
        return v.validate_name(value, "first_name")

    @field_validator("last_name")
    @classmethod
    def _v_last(cls, value):
        return v.validate_name(value, "last_name")

    @field_validator("date_of_birth")
    @classmethod
    def _v_dob(cls, value):
        # Round-trips through the parser so the stored value is always canonical.
        return v.format_dob(v.parse_dob(value))

    @field_validator("sex")
    @classmethod
    def _v_sex(cls, value):
        return v.normalize_sex(value)

    @field_validator("phone_number")
    @classmethod
    def _v_phone(cls, value):
        return v.normalize_phone(value)

    @field_validator("state")
    @classmethod
    def _v_state(cls, value):
        return v.normalize_state(value)

    @field_validator("zip_code")
    @classmethod
    def _v_zip(cls, value):
        return v.normalize_zip(value)

    @field_validator("address_line_1")
    @classmethod
    def _v_addr(cls, value):
        cleaned = v.clean_text(value)
        if not cleaned or len(cleaned) > 200:
            raise v.ValidationProblem(
                "address_line_1", "Please give a street address up to 200 characters."
            )
        return cleaned

    @field_validator("city")
    @classmethod
    def _v_city(cls, value):
        cleaned = v.clean_text(value)
        if not cleaned or len(cleaned) > 100:
            raise v.ValidationProblem("city", "City must be 1 to 100 characters.")
        return cleaned

    # --- Optional-field normalizers ---------------------------------------
    @field_validator("email")
    @classmethod
    def _v_email(cls, value):
        return v.normalize_email(value)

    @field_validator("emergency_contact_phone")
    @classmethod
    def _v_ec_phone(cls, value):
        if not v.clean_text(value):
            return None
        return v.normalize_phone(value, "emergency_contact_phone")

    @field_validator("emergency_contact_name")
    @classmethod
    def _v_ec_name(cls, value):
        if not v.clean_text(value):
            return None
        return v.validate_name(value, "emergency_contact_name")

    @field_validator("insurance_member_id")
    @classmethod
    def _v_member(cls, value):
        return v.normalize_member_id(value)

    @field_validator("preferred_language")
    @classmethod
    def _v_lang(cls, value):
        return v.clean_text(value) or "English"

    @field_validator("address_line_2", "insurance_provider")
    @classmethod
    def _v_optional_text(cls, value):
        return v.clean_text(value)


class PatientUpdate(BaseModel):
    """Partial update. Every field optional; only what is sent gets written.

    Reuses PatientCreate's validators so a field updated via PUT is held to exactly
    the same rules as when it was created.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    address_line_1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    email: Optional[str] = None
    address_line_2: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator(
        "first_name", "last_name", "date_of_birth", "sex", "phone_number",
        "address_line_1", "city", "state", "zip_code", "email",
        "address_line_2", "insurance_provider", "insurance_member_id",
        "preferred_language", "emergency_contact_name", "emergency_contact_phone",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value):
        """Voice tool calls often send "" for a field the caller skipped."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("first_name")
    @classmethod
    def _v_first(cls, value):
        return None if value is None else v.validate_name(value, "first_name")

    @field_validator("last_name")
    @classmethod
    def _v_last(cls, value):
        return None if value is None else v.validate_name(value, "last_name")

    @field_validator("date_of_birth")
    @classmethod
    def _v_dob(cls, value):
        return None if value is None else v.format_dob(v.parse_dob(value))

    @field_validator("sex")
    @classmethod
    def _v_sex(cls, value):
        return None if value is None else v.normalize_sex(value)

    @field_validator("phone_number")
    @classmethod
    def _v_phone(cls, value):
        return None if value is None else v.normalize_phone(value)

    @field_validator("state")
    @classmethod
    def _v_state(cls, value):
        return None if value is None else v.normalize_state(value)

    @field_validator("zip_code")
    @classmethod
    def _v_zip(cls, value):
        return None if value is None else v.normalize_zip(value)

    @field_validator("email")
    @classmethod
    def _v_email(cls, value):
        return v.normalize_email(value)

    @field_validator("emergency_contact_phone")
    @classmethod
    def _v_ec_phone(cls, value):
        return None if value is None else v.normalize_phone(value, "emergency_contact_phone")

    @field_validator("emergency_contact_name")
    @classmethod
    def _v_ec_name(cls, value):
        return None if value is None else v.validate_name(value, "emergency_contact_name")

    @field_validator("insurance_member_id")
    @classmethod
    def _v_member(cls, value):
        return v.normalize_member_id(value)

    @field_validator("city", "address_line_1", "address_line_2",
                     "insurance_provider", "preferred_language")
    @classmethod
    def _v_text(cls, value):
        return v.clean_text(value)


# --------------------------------------------------------------------------
# Read model
# --------------------------------------------------------------------------
class PatientOut(BaseModel):
    """Serialized patient.

    `date_of_birth` and `phone_number` are rendered in human/US form (MM/DD/YYYY and
    (555) 123-4567) because the spec defines them that way and because the dashboard
    and the agent's read-back both consume this shape directly.
    """

    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: str
    sex: str
    phone_number: str
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    @classmethod
    def from_model(cls, patient) -> "PatientOut":
        return cls(
            patient_id=patient.patient_id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            date_of_birth=v.format_dob(patient.date_of_birth),
            sex=patient.sex,
            phone_number=v.format_phone(patient.phone_number),
            email=patient.email,
            address_line_1=patient.address_line_1,
            address_line_2=patient.address_line_2,
            city=patient.city,
            state=patient.state,
            zip_code=patient.zip_code,
            insurance_provider=patient.insurance_provider,
            insurance_member_id=patient.insurance_member_id,
            preferred_language=patient.preferred_language,
            emergency_contact_name=patient.emergency_contact_name,
            emergency_contact_phone=v.format_phone(patient.emergency_contact_phone),
            created_at=patient.created_at,
            updated_at=patient.updated_at,
            deleted_at=patient.deleted_at,
        )


class PatientList(BaseModel):
    count: int
    patients: List[PatientOut]


__all__ = [
    "Envelope",
    "ErrorDetail",
    "PatientCreate",
    "PatientUpdate",
    "PatientOut",
    "PatientList",
    "Any",
]
