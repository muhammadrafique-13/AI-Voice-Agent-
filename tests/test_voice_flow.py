"""Conversation simulator.

Replays realistic sequences of Vapi tool calls through the live webhook, in the order a
real call produces them. This is the closest thing to an automated test of the voice
experience: it cannot judge whether the agent *sounds* good, but it proves the whole
tool contract holds - including the recovery paths that are hard to trigger on purpose
while on the phone.
"""
import json

import pytest

WEBHOOK = "/api/vapi/tool"
HEADERS = {"X-Vapi-Secret": "test-secret"}

CALLER = "+15125557001"


def tool_message(name, arguments, call_id="call-test-1", caller=CALLER):
    """Build the payload Vapi POSTs for a tool call."""
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": caller}},
            "toolCallList": [
                {"id": f"tc-{name}", "name": name, "arguments": arguments}
            ],
        }
    }


def invoke(client, name, arguments, call_id="call-test-1", caller=CALLER):
    resp = client.post(
        WEBHOOK, json=tool_message(name, arguments, call_id, caller), headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["toolCallId"] == f"tc-{name}"
    return results[0]["result"]


BASE = {
    "first_name": "Jane",
    "last_name": "Davies",
    "date_of_birth": "03/05/1985",
    "sex": "Female",
    "phone_number": "5125557001",
    "address_line_1": "412 Oak Street",
    "city": "Austin",
    "state": "Texas",
    "zip_code": "78704",
}


# --- Security -------------------------------------------------------------
def test_webhook_rejects_wrong_secret(client):
    resp = client.post(WEBHOOK, json=tool_message("lookup_patient", {}),
                       headers={"X-Vapi-Secret": "wrong"})
    assert resp.status_code == 401


def test_webhook_rejects_missing_secret(client):
    resp = client.post(WEBHOOK, json=tool_message("lookup_patient", {}))
    assert resp.status_code == 401


# --- The happy path, end to end -------------------------------------------
def test_full_registration_call(client):
    call = "call-happy-1"

    # 1. Silent duplicate check at connect.
    assert "NO_EXISTING_PATIENT" in invoke(client, "lookup_patient", {}, call)

    # 2. Progress checkpointed after the name.
    assert "PROGRESS_SAVED" in invoke(
        client, "save_progress", {"first_name": "Jane", "last_name": "Davies"}, call
    )

    # 3. Server-side read-back, with all fields present.
    confirm = invoke(client, "confirm_details", BASE, call)
    assert "ALL_FIELDS_VALID" in confirm
    assert "Jane Davies" in confirm
    assert "March 5th, 1985" in confirm
    assert "5 1 2, 5 5 5, 7 0 0 1" in confirm  # digits grouped for speech
    assert "Texas" in confirm                   # state spoken in full

    # 4. Caller confirms -> save.
    saved = invoke(client, "register_patient", BASE, call)
    assert saved.startswith("SAVED.")
    patient_id = saved.split("patient_id=")[1].split(".")[0]

    # 5. The record is immediately readable through the REST API.
    body = client.get(f"/patients/{patient_id}").json()["data"]
    assert body["last_name"] == "Davies"
    assert body["state"] == "TX"          # "Texas" normalized on the way in
    assert body["phone_number"] == "(512) 555-7001"

    # 6. Appointment offer (bonus) still works after the save.
    assert "AVAILABLE_SLOTS" in invoke(client, "offer_appointment", {}, call)


# --- Edge cases the brief names explicitly --------------------------------
def test_invalid_dob_reprompts_for_that_field_only(client):
    """'What if the caller says an invalid date of birth?'"""
    result = invoke(
        client, "confirm_details", dict(BASE, date_of_birth="03/05/2099"), "call-dob"
    )
    assert "VALIDATION_ERROR" in result
    assert "field=date_of_birth" in result
    assert "only for that one field" in result
    assert "ALL_FIELDS_VALID" not in result


def test_short_phone_number_is_caught_before_saving(client):
    result = invoke(client, "register_patient", dict(BASE, phone_number="555"), "call-ph")
    assert "VALIDATION_ERROR" in result
    assert "field=phone_number" in result
    assert "NOT saved" in result


def test_spelled_out_correction_is_stored_correctly(client):
    """'Actually, my last name is spelled D-A-V-I-S, not D-A-V-I-E-S.'"""
    call = "call-correction"
    corrected = dict(BASE, last_name="D-A-V-I-S", phone_number="5125557002")

    confirm = invoke(client, "confirm_details", corrected, call)
    assert "ALL_FIELDS_VALID" in confirm
    assert "Jane Davis" in confirm  # letters joined, not stored as "D-A-V-I-S"

    saved = invoke(client, "register_patient", corrected, call)
    patient_id = saved.split("patient_id=")[1].split(".")[0]
    assert client.get(f"/patients/{patient_id}").json()["data"]["last_name"] == "Davis"


def test_dropped_call_leaves_a_recoverable_draft(client):
    """'What if the telephony connection drops mid-call?'"""
    call = "call-dropped"
    invoke(client, "save_progress", {"first_name": "Marcus", "last_name": "Webb"}, call)
    invoke(client, "save_progress", {"date_of_birth": "08/14/1979", "sex": "Male"}, call)
    # ...caller hangs up here. No register_patient ever arrives.

    from app.db import session_scope
    from app.services import patients as service

    with session_scope() as db:
        draft = service.get_draft(db, call)

    assert draft is not None
    assert draft["first_name"] == "Marcus"
    assert draft["date_of_birth"] == "08/14/1979"

    # And it shows up as an open follow-up on the dashboard.
    assert "Marcus" in client.get("/dashboard").text


def test_duplicate_tool_fire_does_not_create_two_patients(client):
    """Models occasionally emit the same tool call twice."""
    call = "call-idempotent"
    payload = dict(BASE, phone_number="5125557003")

    first = invoke(client, "register_patient", payload, call)
    second = invoke(client, "register_patient", payload, call)

    assert first.split("patient_id=")[1] == second.split("patient_id=")[1]
    listed = client.get("/patients", params={"phone_number": "5125557003"}).json()
    assert listed["data"]["count"] == 1


def test_returning_caller_is_recognized_and_can_update(client):
    """Bonus: duplicate detection, then an update instead of a second record."""
    invoke(client, "register_patient", dict(BASE, phone_number="5125557004"),
           "call-first", caller="+15125557004")

    found = invoke(client, "lookup_patient", {}, "call-second", caller="+15125557004")
    assert "EXISTING_PATIENT_FOUND" in found
    assert "Would you like to update your information instead?" in found

    patient_id = found.split("patient_id=")[1].split(";")[0]
    updated = invoke(
        client, "update_patient",
        {"patient_id": patient_id, "city": "Round Rock", "insurance_provider": "Aetna"},
        "call-second",
    )
    assert "UPDATED" in updated

    body = client.get(f"/patients/{patient_id}").json()["data"]
    assert body["city"] == "Round Rock"
    assert body["insurance_provider"] == "Aetna"
    # Still exactly one record for that number.
    assert client.get("/patients", params={"phone_number": "5125557004"}).json()["data"]["count"] == 1


def test_update_without_patient_id_asks_for_lookup(client):
    result = invoke(client, "update_patient", {"city": "Austin"}, "call-noid")
    assert "MISSING_PATIENT_ID" in result


def test_unknown_tool_degrades_gracefully(client):
    result = invoke(client, "transfer_to_mars", {}, "call-unknown")
    assert "UNKNOWN_TOOL" in result


# --- Protocol robustness ---------------------------------------------------
def test_openai_shaped_tool_calls_are_accepted(client):
    """Vapi switches between `toolCallList` and OpenAI-shaped `toolCalls`."""
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-shape", "customer": {"number": CALLER}},
            "toolCalls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    # arguments as a JSON *string*, which some providers send
                    "function": {"name": "lookup_patient", "arguments": json.dumps({})},
                }
            ],
        }
    }
    resp = client.post(WEBHOOK, json=payload, headers=HEADERS)
    assert resp.status_code == 200
    assert "PATIENT" in resp.json()["results"][0]["result"]


@pytest.mark.parametrize("blank", ["", "N/A", "null", "unknown"])
def test_llm_placeholder_values_are_treated_as_absent(client, blank):
    """Models fill optional fields with junk rather than omitting them."""
    result = invoke(
        client, "confirm_details",
        dict(BASE, phone_number="5125557005", email=blank, insurance_provider=blank),
        f"call-blank-{blank or 'empty'}",
    )
    assert "ALL_FIELDS_VALID" in result
    assert blank not in result or blank == ""


def test_end_of_call_report_is_persisted(client):
    """Bonus: transcript, summary and recording stored against the call."""
    call = "call-eoc"
    saved = invoke(client, "register_patient", dict(BASE, phone_number="5125557006"), call)
    patient_id = saved.split("patient_id=")[1].split(".")[0]

    resp = client.post(
        WEBHOOK,
        json={
            "message": {
                "type": "end-of-call-report",
                "call": {"id": call, "customer": {"number": CALLER}},
                "endedReason": "customer-ended-call",
                "summary": "Registered Jane Davies as a new patient.",
                "recordingUrl": "https://example.com/recording.wav",
                "artifact": {"transcript": "AI: Thanks for calling...\nUser: Hi..."},
            }
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200

    from app.db import session_scope
    from app.models import CallTranscript
    from sqlalchemy import select

    with session_scope() as db:
        record = db.execute(
            select(CallTranscript).where(CallTranscript.call_id == call)
        ).scalars().first()

    assert record is not None
    assert record.patient_id == patient_id  # linked across two separate webhook calls
    assert record.ended_reason == "customer-ended-call"
    assert record.recording_url == "https://example.com/recording.wav"
    assert "Thanks for calling" in record.transcript


def test_unhandled_message_types_return_200(client):
    """A non-200 makes Vapi retry, and a retry mid-call is worse than a dropped event."""
    for msg_type in ("status-update", "speech-update", "conversation-update", "hang"):
        resp = client.post(
            WEBHOOK,
            json={"message": {"type": msg_type, "call": {"id": "call-x"}}},
            headers=HEADERS,
        )
        assert resp.status_code == 200, msg_type
