"""Generate (and optionally publish) the Vapi assistant configuration.

Config as code, for two reasons:

1. The system prompt lives in exactly one place - `voice/system_prompt.md`. This script
   lifts it out of the fenced block so the documented prompt and the deployed prompt
   cannot drift apart.
2. Clicking through a vendor dashboard leaves no diff. This leaves a reviewable one.

Usage:
    python -m scripts.build_assistant                 # write voice/vapi_assistant.json
    python -m scripts.build_assistant --publish       # also create/update it in Vapi

Env:
    PUBLIC_BASE_URL      https://your-app.vercel.app   (required for --publish)
    VAPI_SERVER_SECRET   shared secret sent as X-Vapi-Secret on every webhook
    VAPI_API_KEY         Vapi *private* key (required for --publish)
    VAPI_ASSISTANT_ID    if set, updates that assistant instead of creating a new one
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

PROMPT_FILE = os.path.join(ROOT, "voice", "system_prompt.md")
OUTPUT_FILE = os.path.join(ROOT, "voice", "vapi_assistant.json")

VAPI_API = "https://api.vapi.ai"

FIRST_MESSAGE = (
    "Thanks for calling Westside Family Medicine, this is Riley. "
    "Are you calling to register as a new patient?"
)


# --------------------------------------------------------------------------
# Shared JSON-schema fragments for the tool definitions
# --------------------------------------------------------------------------
def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


REQUIRED_FIELDS = {
    "first_name": _str("Caller's legal first name. Join spelled-out letters into a word."),
    "last_name": _str("Caller's legal last name."),
    "date_of_birth": _str("Date of birth as MM/DD/YYYY."),
    "sex": {
        "type": "string",
        "enum": ["Male", "Female", "Other", "Decline to Answer"],
        "description": "Exactly one of the four allowed values.",
    },
    "phone_number": _str(
        "Best callback number. A 10-digit U.S. number, or an international number "
        "with its country code included, e.g. +923001234567."
    ),
    "address_line_1": _str("Street number and name."),
    "city": _str("City name."),
    "state": _str("Two-letter U.S. state abbreviation, e.g. TX."),
    "zip_code": _str("5-digit ZIP, or ZIP+4 as 12345-6789."),
}

OPTIONAL_FIELDS = {
    "email": _str("Email address. Omit entirely if not provided."),
    "address_line_2": _str("Apartment, suite or unit. Omit if none."),
    "insurance_provider": _str("Insurance company name."),
    "insurance_member_id": _str("Member or subscriber ID, letters and digits."),
    "preferred_language": _str("Preferred spoken language. Defaults to English."),
    "emergency_contact_name": _str("Emergency contact's full name."),
    "emergency_contact_phone": _str(
        "Emergency contact's number. U.S. 10-digit, or international with country code."
    ),
}

ALL_FIELDS = dict(REQUIRED_FIELDS, **OPTIONAL_FIELDS)


def _tool(name: str, description: str, properties: dict, required=None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def build_tools(webhook_url: str, secret: str) -> list:
    """Six tools. Each one is attached to the same webhook endpoint."""
    server = {"url": webhook_url}
    if secret:
        server["secret"] = secret

    tools = [
        _tool(
            "lookup_patient",
            "Check whether a patient record already exists for a phone number. Call this "
            "silently at the very start of every call, before asking any questions. "
            "Defaults to the caller's own number when phone_number is omitted.",
            {"phone_number": _str("Optional. Defaults to the inbound caller ID.")},
        ),
        _tool(
            "save_progress",
            "Checkpoint the information collected so far. Call this after each group of "
            "answers (name, then date of birth and sex, then phone, then address) so "
            "nothing is lost if the call drops. Send only the fields you already have. "
            "This is silent bookkeeping - never mention it to the caller.",
            dict(ALL_FIELDS),
        ),
        _tool(
            "confirm_details",
            "Validate every collected field and get back the exact confirmation sentence "
            "to read aloud. Call this once all required fields are collected and the "
            "caller has answered the optional-information question, BEFORE reading "
            "anything back and BEFORE calling register_patient.",
            dict(ALL_FIELDS),
            required=list(REQUIRED_FIELDS),
        ),
        _tool(
            "register_patient",
            "Save the patient record permanently. Call this ONLY after the caller has "
            "explicitly confirmed the details you read back to them. Send the complete "
            "set of values, not just the changed ones.",
            dict(ALL_FIELDS),
            required=list(REQUIRED_FIELDS),
        ),
        _tool(
            "update_patient",
            "Update an existing patient record. Use the patient_id returned by "
            "lookup_patient, and send only the fields the caller wants to change.",
            dict({"patient_id": _str("patient_id from lookup_patient.")}, **ALL_FIELDS),
            required=["patient_id"],
        ),
        _tool(
            "offer_appointment",
            "Get available new-patient appointment slots, or hold one the caller chose. "
            "Call with no arguments to fetch the options, then again with preferred_time "
            "once they pick one.",
            {"preferred_time": _str("The slot the caller chose, in their own words.")},
        ),
    ]

    for tool in tools:
        tool["server"] = server
    return tools


def load_prompt() -> str:
    """Lift the prompt out of the ```text fence in system_prompt.md."""
    with open(PROMPT_FILE, encoding="utf-8") as fh:
        content = fh.read()
    blocks = re.findall(r"```text\n(.*?)```", content, re.DOTALL)
    if not blocks:
        raise SystemExit(f"No ```text block found in {PROMPT_FILE}")
    return blocks[0].strip()


def build_assistant(base_url: str, secret: str) -> dict:
    webhook = f"{base_url.rstrip('/')}/api/vapi/tool"
    server = {"url": webhook}
    if secret:
        server["secret"] = secret

    return {
        "name": "Patient Intake - Riley",
        "firstMessage": FIRST_MESSAGE,
        # Speak the greeting immediately instead of waiting on the first LLM token -
        # the pause before a first "hello" is the most noticeable latency on a call.
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            # Warm enough to vary phrasing, low enough not to improvise policy.
            "temperature": 0.3,
            "messages": [{"role": "system", "content": load_prompt()}],
            "tools": build_tools(webhook, secret),
        },
        "voice": {
            # Vapi's bundled voices need no third-party credential, which removes the
            # most common cause of an assistant that answers and then goes silent.
            "provider": "vapi",
            "voiceId": "Elliot",
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en",
            # Proper nouns the transcriber otherwise mangles on this call type.
            "keywords": ["Medicaid:2", "Medicare:2", "Aetna:2", "Cigna:2", "Humana:2"],
        },
        "server": server,
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
        # Resilience settings - see README "Edge cases".
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,
        "backgroundSound": "office",
        "endCallFunctionEnabled": True,
        "endCallPhrases": ["goodbye", "have a good day", "we'll see you soon"],
        "analysisPlan": {
            "summaryPrompt": (
                "In two sentences, summarize this patient intake call: who called, "
                "whether a record was created or updated, and anything left unresolved."
            )
        },
    }


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------
def _request(method: str, path: str, api_key: str, payload=None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{VAPI_API}{path}", data=body, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    # The API sits behind a CDN that rejects urllib's default agent with 403/1010.
    req.add_header("User-Agent", "patient-intake-setup/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Vapi API {exc.code}: {exc.read().decode()[:600]}")


def publish(assistant: dict, api_key: str) -> None:
    assistant_id = os.getenv("VAPI_ASSISTANT_ID", "").strip()
    if assistant_id:
        # PATCH replaces nested objects wholesale, so `model` must be sent complete -
        # sending only {"model": {"model": "..."}} silently wipes the tools and prompt.
        result = _request("PATCH", f"/assistant/{assistant_id}", api_key, assistant)
        print(f"Updated assistant {assistant_id}")
    else:
        result = _request("POST", "/assistant", api_key, assistant)
        print(f"Created assistant {result.get('id')}")
        print("Set VAPI_ASSISTANT_ID to this id to update it in place next time.")

    tools = ((result.get("model") or {}).get("tools")) or []
    prompt = ((result.get("model") or {}).get("messages") or [{}])[0].get("content", "")
    # Always verify after a write - a silently emptied prompt only shows up on a call.
    print(f"Verified: {len(tools)} tools, prompt {len(prompt)} chars")
    if len(tools) != 6 or len(prompt) < 1000:
        raise SystemExit("Assistant did not persist correctly - re-run before calling.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="push to the Vapi API")
    args = parser.parse_args()

    base_url = os.getenv("PUBLIC_BASE_URL", "https://REPLACE-ME.vercel.app")
    secret = os.getenv("VAPI_SERVER_SECRET", "")

    assistant = build_assistant(base_url, secret)

    # The written file is a template for pasting into the dashboard, so the real secret
    # never lands in the repository.
    redacted = json.loads(json.dumps(assistant).replace(secret, "${VAPI_SERVER_SECRET}") if secret else json.dumps(assistant))
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(redacted, fh, indent=2)
        fh.write("\n")
    print(f"Wrote {OUTPUT_FILE}")
    print(f"  webhook: {base_url.rstrip('/')}/api/vapi/tool")
    print(f"  prompt:  {len(load_prompt())} chars")
    print(f"  tools:   {len(assistant['model']['tools'])}")

    if args.publish:
        api_key = os.getenv("VAPI_API_KEY", "").strip()
        if not api_key:
            raise SystemExit("VAPI_API_KEY is required for --publish")
        if "REPLACE-ME" in base_url:
            raise SystemExit("Set PUBLIC_BASE_URL to your deployed URL before publishing")
        publish(assistant, api_key)


if __name__ == "__main__":
    main()
