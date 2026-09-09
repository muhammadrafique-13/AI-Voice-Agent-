"""REST API for patient records.

Every response uses the envelope { "data": ..., "error": ... }.
Status codes: 200 OK, 201 Created, 400 Bad Request, 404 Not Found,
422 Unprocessable Entity (validation), 500 Internal Server Error.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    Envelope,
    PatientCreate,
    PatientList,
    PatientOut,
    PatientUpdate,
)
from app.services import patients as service
from app.validators import ValidationProblem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=Envelope[PatientList])
def list_patients(
    last_name: Optional[str] = Query(None, description="Case-insensitive exact match"),
    date_of_birth: Optional[str] = Query(None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: Optional[str] = Query(None, description="Any U.S. format"),
    include_deleted: bool = Query(False, description="Include soft-deleted records"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List patients, optionally filtered. Soft-deleted records are hidden by default."""
    try:
        rows = service.list_patients(
            db,
            last_name=last_name,
            date_of_birth=date_of_birth,
            phone_number=phone_number,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )
    except ValidationProblem as exc:
        # A bad filter value is a client error, not a server error.
        raise HTTPException(status_code=400, detail={"field": exc.field, "message": exc.message})

    payload = PatientList(
        count=len(rows), patients=[PatientOut.from_model(r) for r in rows]
    )
    return Envelope[PatientList](data=payload)


@router.get("/{patient_id}", response_model=Envelope[PatientOut])
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        patient = service.get_patient(db, patient_id)
    except service.PatientNotFound:
        raise HTTPException(status_code=404, detail={"message": "Patient not found."})
    return Envelope[PatientOut](data=PatientOut.from_model(patient))


@router.post("", response_model=Envelope[PatientOut], status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreate, response: Response, db: Session = Depends(get_db)
):
    """Create a patient. Returns 201 with the created record including patient_id."""
    patient = service.create_patient(db, payload, source="api")
    response.headers["Location"] = f"/patients/{patient.patient_id}"
    return Envelope[PatientOut](data=PatientOut.from_model(patient))


@router.put("/{patient_id}", response_model=Envelope[PatientOut])
def update_patient(
    patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)
):
    """Partial update - send only the fields you want to change."""
    try:
        patient = service.update_patient(db, patient_id, payload, source="api")
    except service.PatientNotFound:
        raise HTTPException(status_code=404, detail={"message": "Patient not found."})
    return Envelope[PatientOut](data=PatientOut.from_model(patient))


@router.delete("/{patient_id}", response_model=Envelope[PatientOut])
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    """Soft delete: stamps deleted_at. The row is retained for audit purposes."""
    try:
        patient = service.soft_delete_patient(db, patient_id)
    except service.PatientNotFound:
        raise HTTPException(status_code=404, detail={"message": "Patient not found."})
    return Envelope[PatientOut](data=PatientOut.from_model(patient))
