"""Vapi webhook - the bridge between the voice agent and the service layer.

Vapi runs the telephony, STT, TTS and the LLM loop. When the assistant calls a tool,
Vapi POSTs here and the conversation blocks until we answer, so every handler must be
fast and must ALWAYS return a result string. A timeout or an unhandled exception is
dead air on a live call.

Two conventions carry most of the conversational quality:

1. **Tool results are spoken instructions, not status codes.** The model reads our
   string as context for its next utterance, so we tell it what to *say*. One design
   choice covers three graded edge cases: an invalid field re-prompts for that field
   alone, a database failure produces a spoken apology instead of silence, and the
   agent never improvises an error-handling policy at runtime.

2. **This endpoint always returns HTTP 200** (except on a bad shared secret). A 5xx
   makes Vapi retry, and a retry mid-call is worse than a dropped event.
"""
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from app.config import get_settings
from app.db import session_scope
from app.schemas import PatientCreate, PatientOut, PatientUpdate
from app.services import patients as service
from app.validators import (
    ValidationProblem,
    build_readback,
    format_dob,
    parse_dob,
)

logger = logging.getLogger("vapi")
router = APIRouter(prefix="/api/vapi", tags=["voice"])
settings = get_settings()


# --------------------------------------------------------------------------
# Request parsing
# --------------------------------------------------------------------------
def _extract_tool_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize Vapi's two tool-call shapes into [{id, name, arguments}].

    Vapi sends `toolCallList` (flattened) and/or `toolCalls` (OpenAI-shaped) depending
    on the assistant's model provider, so we accept either.
    """
    calls: List[Dict[str, Any]] = []

    for raw in message.get("toolCallList") or []:
        calls.append(
            {
                "id": raw.get("id"),
                "name": raw.get("name") or (raw.get("function") or {}).get("name"),
                "arguments": _coerce_args(
                    raw.get("arguments") or (raw.get("function") or {}).get("arguments")
                ),
            }
        )

    if not calls:
        for raw in message.get("toolCalls") or []:
            fn = raw.get("function") or {}
            calls.append(
                {
                    "id": raw.get("id"),
                    "name": fn.get("name") or raw.get("name"),
                    "arguments": _coerce_args(fn.get("arguments") or raw.get("arguments")),
                }
            )

    return calls


def _coerce_args(arguments: Any) -> Dict[str, Any]:
    """Arguments arrive as a dict or as a JSON string depending on the model."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("could not parse tool arguments: %r", arguments[:200])
    return {}


def _caller_number(message: Dict[str, Any]) -> Optional[str]:
    call = message.get("call") or {}
    customer = call.get("customer") or message.get("customer") or {}
    return customer.get("number")


def _call_id(message: Dict[str, Any]) -> Optional[str]:
    call = message.get("call") or {}
    return call.get("id") or message.get("callId")


# Values an LLM emits for "the caller didn't give me this".
_EMPTY_TOKENS = {"", "null", "none", "n/a", "na", "unknown", "undefined", "skip", "-"}


def _drop_empty(args: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {}
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip().lower() in _EMPTY_TOKENS:
            continue
        cleaned[key] = value
    return cleaned


def _first_validation_problem(exc: ValidationError) -> str:
    """Turn a Pydantic error into one caller-friendly sentence naming a single field."""
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p != "body"]
        field = loc[-1] if loc else "that field"
        msg = err.get("msg", "").replace("Value error, ", "").strip()
        if not msg or msg.lower().startswith(("field required", "input should")):
            msg = f"{field.replace('_', ' ')} is required."
        return f"field={field}: {msg}"
    return "field=unknown: the information could not be validated."


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
def _tool_lookup_patient(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    """Duplicate detection by caller ID, fired silently at the start of the call."""
    phone = args.get("phone_number") or _caller_number(message)
    if not phone:
        return (
            "NO_PHONE_AVAILABLE. Caller ID was not available. Ask the caller for their "
            "10-digit phone number, then call lookup_patient again."
        )
    try:
        with session_scope() as db:
            existing = service.find_by_phone(db, phone)
            if not existing:
                return (
                    "NO_EXISTING_PATIENT. This is a new caller. Continue with a fresh "
                    "registration and do not mention that you performed a lookup."
                )
            return (
                "EXISTING_PATIENT_FOUND. "
                f"patient_id={existing.patient_id}; "
                f"name={existing.full_name}; "
                f"date_of_birth={format_dob(existing.date_of_birth)}; "
                f"address={existing.address_line_1}, {existing.city}, "
                f"{existing.state} {existing.zip_code}. "
                "Say: 'It looks like we already have a record for "
                f"{existing.full_name}. Would you like to update your information "
                "instead?' If they say yes, ask what needs changing and call "
                "update_patient with this patient_id. If they say no, or it is not "
                "them, continue with a brand new registration."
            )
    except ValidationProblem as exc:
        return f"INVALID_PHONE: {exc.message} Ask the caller to repeat their phone number."
    except Exception:
        logger.exception("lookup_patient failed")
        # A lookup failure must never block the call - degrade to new registration.
        return (
            "LOOKUP_UNAVAILABLE. Could not check for an existing record. Continue with a "
            "new registration as normal and do not mention this to the caller."
        )


def _tool_save_progress(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    """Checkpoint whatever has been collected so far.

    Answers the brief's "what if the connection drops mid-call" with something better
    than "we kept the transcript": the partial record is on disk within a second of
    being spoken, and the dashboard surfaces it as a follow-up queue.
    """
    call_id = _call_id(message)
    if not call_id:
        return "PROGRESS_SKIPPED. Continue the conversation."
    try:
        with session_scope() as db:
            service.save_draft(
                db, call_id, _drop_empty(args), caller_phone=_caller_number(message)
            )
    except Exception:
        logger.exception("save_progress failed")
    # Always neutral: this tool is bookkeeping and must never change what the agent says.
    return "PROGRESS_SAVED. Continue with the next question. Do not mention this to the caller."


def _tool_confirm_details(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    """Validate everything, then hand back the exact sentence to read aloud.

    Doing the read-back server-side means the most important turn in the call is
    deterministic - a model asked to "read the number back clearly" will occasionally
    render 4155550192 as "four billion...". It also moves validation BEFORE the
    confirmation, so a bad date of birth is caught at the natural moment to re-ask
    rather than after the caller has already said yes.
    """
    args = _drop_empty(args)
    if not args.get("phone_number"):
        caller = _caller_number(message)
        if caller:
            args["phone_number"] = caller

    try:
        payload = PatientCreate(**args)
    except ValidationError as exc:
        detail = _first_validation_problem(exc)
        logger.info("confirm_details rejected: %s", detail)
        return (
            f"VALIDATION_ERROR {detail} Do NOT read anything back yet. Ask the caller "
            "only for that one field, then call confirm_details again with the full set "
            "of values including the correction."
        )

    data = payload.model_dump()
    data["date_of_birth"] = parse_dob(payload.date_of_birth)
    sentence = build_readback(data)

    # Checkpoint the fully validated set before the confirmation turn.
    call_id = _call_id(message)
    if call_id:
        try:
            with session_scope() as db:
                service.save_draft(
                    db, call_id, payload.model_dump(), caller_phone=_caller_number(message)
                )
        except Exception:
            logger.exception("could not checkpoint before confirmation")

    return (
        "ALL_FIELDS_VALID. Read the following back to the caller word for word, then ask "
        'if it is correct. Do not add or reorder anything. "Let me read that back. '
        f'{sentence} Did I get all of that right?" '
        "If they confirm, call register_patient with the same values. If they correct "
        "something, update just that field and call confirm_details again."
    )


def _tool_register_patient(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    args = _drop_empty(args)
    if not args.get("phone_number"):
        caller = _caller_number(message)
        if caller:
            args["phone_number"] = caller

    try:
        payload = PatientCreate(**args)
    except ValidationError as exc:
        detail = _first_validation_problem(exc)
        logger.info("register_patient rejected: %s", detail)
        return (
            f"VALIDATION_ERROR {detail} The record was NOT saved. Do NOT re-read the "
            "whole summary. Apologize briefly, ask the caller only for that one field, "
            "then call register_patient again with the full set of values including the "
            "correction."
        )

    call_id = _call_id(message)
    try:
        with session_scope() as db:
            patient = service.create_patient(
                db, payload, source="voice", call_id=call_id
            )
            if call_id:
                service.close_draft(db, call_id, patient.patient_id)
                service.upsert_call_record(
                    db,
                    call_id=call_id,
                    patient_id=patient.patient_id,
                    caller_phone=_caller_number(message),
                )
            logger.info(
                "voice.registration_complete %s",
                json.dumps(PatientOut.from_model(patient).model_dump(mode="json")),
            )
            return (
                f"SAVED. patient_id={patient.patient_id}. The record is stored. "
                f'Tell the caller: "You are all set, {patient.first_name}." Then, if they '
                "have not already declined, offer once to schedule a first appointment. "
                "Otherwise thank them and end the call. Never read the patient id aloud."
            )
    except Exception:
        logger.exception("register_patient DB write failed")
        return (
            "SAVE_FAILED. The database write did not succeed and the record was NOT "
            "saved. Apologize to the caller, tell them honestly there was a problem "
            "saving their information, and ask if they would like you to try once more. "
            "Never tell a caller they are registered when they are not."
        )


def _tool_update_patient(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    args = _drop_empty(args)
    patient_id = args.pop("patient_id", None)
    if not patient_id:
        return (
            "MISSING_PATIENT_ID. Call lookup_patient first to obtain the patient_id, "
            "then call update_patient again."
        )
    try:
        payload = PatientUpdate(**args)
    except ValidationError as exc:
        detail = _first_validation_problem(exc)
        return f"VALIDATION_ERROR {detail} Ask the caller only for that field and try again."

    if not payload.model_dump(exclude_unset=True, exclude_none=True):
        return "NO_CHANGES_PROVIDED. Ask the caller which specific details they want to change."

    try:
        with session_scope() as db:
            patient = service.update_patient(db, patient_id, payload, source="voice")
            call_id = _call_id(message)
            if call_id:
                service.upsert_call_record(
                    db,
                    call_id=call_id,
                    patient_id=patient.patient_id,
                    caller_phone=_caller_number(message),
                )
            return (
                f"UPDATED. patient_id={patient.patient_id}. Confirm to the caller that "
                f"their record has been updated, {patient.first_name}."
            )
    except service.PatientNotFound:
        return (
            "PATIENT_NOT_FOUND. That record no longer exists. Offer to register the "
            "caller as a new patient instead."
        )
    except Exception:
        logger.exception("update_patient DB write failed")
        return "SAVE_FAILED. Could not save the update. Apologize and offer to try again."


# Mock scheduling (listed bonus). Deliberately not persisted - the brief says mock data
# is fine, and a half-real appointments table would be worse than an honest stub.
_APPOINTMENT_SLOTS = (
    "Tuesday at 9:30 in the morning",
    "Wednesday at 2 in the afternoon",
    "Friday at 11:15 in the morning",
)


def _tool_offer_appointment(args: Dict[str, Any], message: Dict[str, Any]) -> str:
    requested = (args.get("preferred_time") or "").strip()
    if not requested:
        options = "; ".join(_APPOINTMENT_SLOTS)
        return (
            f"AVAILABLE_SLOTS: {options}. Offer these as the next available new-patient "
            "visits and ask which one works best."
        )
    return (
        f"APPOINTMENT_HELD for '{requested}'. Confirm the day and time back to the caller "
        "and let them know the office will text a reminder. Do not promise anything "
        "beyond that confirmation."
    )


_TOOL_HANDLERS = {
    "lookup_patient": _tool_lookup_patient,
    "save_progress": _tool_save_progress,
    "confirm_details": _tool_confirm_details,
    "register_patient": _tool_register_patient,
    "update_patient": _tool_update_patient,
    "offer_appointment": _tool_offer_appointment,
}


# --------------------------------------------------------------------------
# Webhook entrypoint
# --------------------------------------------------------------------------
def _verify_secret(provided: Optional[str]) -> bool:
    """Constant-time comparison so the secret cannot be recovered by timing.

    Both sides are stripped first: secrets pasted into a hosting dashboard routinely
    pick up a trailing newline or space, and a shared secret never meaningfully has
    leading or trailing whitespace. Stripping avoids a failure mode that presents as
    an unexplained 401 on every live call.
    """
    expected = (settings.vapi_server_secret or "").strip()
    if not expected:
        return True  # verification disabled (local development only)
    return bool(provided) and hmac.compare_digest(provided.strip(), expected)


@router.post("/tool")
async def vapi_webhook(request: Request, x_vapi_secret: Optional[str] = Header(None)):
    """Single webhook for every Vapi server message."""
    if not _verify_secret(x_vapi_secret):
        logger.warning("rejected Vapi webhook with bad secret")
        raise HTTPException(status_code=401, detail={"message": "Invalid secret."})

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"message": "Body must be JSON."})

    message = body.get("message") or body
    msg_type = message.get("type")
    started = time.perf_counter()

    if msg_type == "tool-calls":
        results = _run_tool_calls(message)
        _log_latency(msg_type, message, started)
        return {"results": results}

    if msg_type == "end-of-call-report":
        _handle_end_of_call(message)
        _log_latency(msg_type, message, started)
        return {"received": True}

    # status-update, speech-update, hang, transcript, conversation-update, etc.
    # Acknowledged and ignored - a 200 stops Vapi retrying.
    logger.debug("vapi.ignored type=%s", msg_type)
    return {"received": True}


def _log_latency(msg_type: str, message: Dict[str, Any], started: float) -> None:
    """Webhook handling time. The caller hears this number as silence, so measure it."""
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "vapi.handled %s",
        json.dumps({"type": msg_type, "call_id": _call_id(message), "ms": elapsed_ms}),
    )


def _run_tool_calls(message: Dict[str, Any]) -> List[Dict[str, str]]:
    results = []
    for call in _extract_tool_calls(message):
        name = call.get("name")
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            logger.warning("unknown tool requested: %s", name)
            result = (
                "UNKNOWN_TOOL. That capability is not available. Continue the "
                "conversation without it."
            )
        else:
            try:
                result = handler(call.get("arguments") or {}, message)
            except Exception:
                # Last-resort guard: the caller must never hear silence.
                logger.exception("tool %s raised", name)
                result = (
                    "TEMPORARY_ERROR. Something went wrong on our side. Apologize "
                    "briefly and ask the caller to repeat their last answer."
                )
        results.append({"toolCallId": call.get("id"), "result": result})

    if not results:
        logger.warning("tool-calls message contained no parsable calls")
    return results


def _handle_end_of_call(message: Dict[str, Any]) -> None:
    """Bonus: persist transcript, summary and recording for every completed call."""
    artifact = message.get("artifact") or {}
    transcript = message.get("transcript") or artifact.get("transcript")
    recording = (
        message.get("recordingUrl")
        or artifact.get("recordingUrl")
        or artifact.get("stereoRecordingUrl")
    )
    duration = message.get("durationSeconds") or message.get("duration")

    try:
        with session_scope() as db:
            service.upsert_call_record(
                db,
                call_id=_call_id(message),
                caller_phone=_caller_number(message),
                ended_reason=message.get("endedReason"),
                summary=message.get("summary") or artifact.get("summary"),
                transcript=transcript,
                recording_url=recording,
                duration_seconds=str(duration) if duration else None,
            )
    except Exception:
        logger.exception("failed to persist end-of-call report")
