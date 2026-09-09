# Voice AI Patient Registration

A voice agent that answers a real US phone number, collects standard U.S. patient
demographics in natural conversation, validates and persists them, and exposes them
through a REST API and a web dashboard.

> **Take-home assessment for CareCloud** — Voice AI / Conversational AI Engineer.

---

## Live demo

| | |
| --- | --- |
| 📞 **Call the agent** | **+1 (732) 782-5627** |
| 🔌 **API base URL** | `TODO_BASE_URL` |
| 📊 **Dashboard** | `TODO_BASE_URL/dashboard` |
| 📖 **Interactive API docs** | `TODO_BASE_URL/docs` |
| ❤️ **Health check** | `TODO_BASE_URL/health` |

No credentials are needed to read the API or the dashboard. Two seed patients (Jane Doe,
Luis Ramirez) are pre-loaded so nothing looks empty before your first call.

**Fastest way to verify it works end to end:**

```bash
# 1. Call the number and register yourself.
# 2. Then find your record:
curl "TODO_BASE_URL/patients?last_name=YOUR_LAST_NAME"
```

---

## Architecture

```
   Caller
     │  PSTN
     ▼
┌─────────────────────────────┐
│  Vapi                       │   Telephony, speech-to-text, text-to-speech,
│  (telephony + STT/TTS +     │   turn-taking, barge-in, and the LLM loop.
│   GPT-4o orchestration)     │   We supply the system prompt + 6 tool schemas.
└──────────────┬──────────────┘
               │  HTTPS  POST /api/vapi/tool
               ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI on Vercel                                      │
│                                                         │
│   routers/vapi.py ──┐                                   │
│                     ├──► services/patients.py ──► ORM   │
│   routers/patients.py ─┘        ▲                       │
│                                 │                       │
│   routers/dashboard.py ─────────┘                       │
│                                                         │
│   validators.py — speech normalization, shared by both  │
└──────────────────────────┬──────────────────────────────┘
                           ▼
                  ┌──────────────────┐
                  │  Neon Postgres   │
                  └──────────────────┘
```

### The one decision that shapes everything

**The voice agent and the REST API share a single write path.** Both routers call
`app/services/patients.py`, which is the only module that touches the ORM. A record
created by phone passes through byte-for-byte the same validation and the same
constraints as one created by `curl`. There is no second, weaker path into the database.

Everything else follows from that: `validators.py` is shared, so `POST /patients` accepts
`"California"` and `"March 5 1985"` exactly like the phone call does, and the phone call
is held to the same `422`-worthy rules as the API.

### Layout

```
app/
  main.py               app factory, exception handlers → response envelope
  config.py             environment resolution (no secrets in source)
  db.py                 engine + session (NullPool for serverless)
  models.py             SQLAlchemy schema: constraints, indexes
  validators.py         speech normalization + validation (shared)
  schemas.py            Pydantic request/response models
  services/patients.py  ← the only module that issues queries
  routers/
    patients.py         REST API
    vapi.py             voice webhook (6 tools)
    dashboard.py        server-rendered HTML
voice/
  system_prompt.md      system prompt + design rationale (source of truth)
  vapi_assistant.json   generated assistant config
scripts/
  init_db.py            create schema
  seed.py               demo patients
  build_assistant.py    generate / publish the Vapi assistant
tests/                  77 tests: unit, API integration, conversation simulator
api/index.py            Vercel entry point
run.py                  local entry point
```

---

## Tech stack, and why

| Layer | Choice | Reasoning |
| --- | --- | --- |
| **Telephony + voice** | Vapi | Building STT/TTS/barge-in from scratch would consume the entire time budget on solved problems. Vapi supplies the number, the speech pipeline and the LLM loop; the interesting work — prompt design, tool contracts, data integrity — stays mine. The brief's own FAQ encourages this. |
| **LLM** | GPT-4o via Vapi | Handles mid-sentence corrections and out-of-order answers noticeably better than smaller models, which is 20% of the score. Bundled with Vapi, so there is no separate provider credential that can fail *at call time*. |
| **Backend** | FastAPI | Pydantic gives declarative server-side validation, which the brief explicitly demands ("do not rely solely on the voice agent"). Free OpenAPI docs at `/docs` are a deliverable at no cost. |
| **Database** | Postgres (Neon) | **Vercel's filesystem is ephemeral**, so SQLite would be wiped between cold starts and would fail the "register on Call 1, query on Call 2" test outright. Neon is free, has no cold-start penalty worth worrying about, and is real Postgres. SQLite remains the zero-config local fallback. |
| **ORM** | SQLAlchemy Core/ORM | Same models run on SQLite and Postgres, so local tests exercise the production schema — including the CHECK constraints. |
| **Hosting** | Vercel | Free, no card, negligible cold start. Render's free tier sleeps after 15 minutes and takes ~50s to wake — that is dead air on a reviewer's call. |
| **Dashboard** | Server-rendered HTML, no framework | Must survive a cold start with no build step. A SPA would add a toolchain for a page that shows one table. |

---

## Setup

### Run locally (2 minutes, no accounts needed)

```bash
git clone <repo-url> && cd voice-patient-registration
python -m venv .venv && .venv/Scripts/activate     # Windows
# source .venv/bin/activate                        # macOS / Linux

pip install -r requirements-dev.txt
python -m scripts.init_db     # creates ./patients.db (SQLite fallback)
python -m scripts.seed        # 2 demo patients
python run.py                 # http://localhost:8000
```

Then open <http://localhost:8000/dashboard> or:

```bash
curl http://localhost:8000/patients | python -m json.tool
```

### Run the tests

```bash
pytest -q            # 77 tests, ~3s, uses a throwaway SQLite file
```

### Deploy

<details>
<summary><b>1. Database — Neon</b></summary>

Create a project at [neon.tech](https://neon.tech) (region **US East**, to match Vercel's
default and keep the webhook→DB hop short). Copy the connection string.

```bash
export DATABASE_URL='postgresql://...?sslmode=require'
python -m scripts.init_db
python -m scripts.seed
```
</details>

<details>
<summary><b>2. Hosting — Vercel</b></summary>

Import the repo at [vercel.com/new](https://vercel.com/new).

- **Framework Preset: Other.** Auto-detection will otherwise apply a preset that claims
  the `/` route and demands its own entry file.
- Environment variables: `DATABASE_URL`, `VAPI_SERVER_SECRET`, `APP_ENV=production`.
- **Settings → Deployment Protection → Disable.**

> ⚠️ Deployment Protection is **on by default** and is the single most likely way to ship
> a broken submission. Every request redirects to an SSO login — but *you* are signed in,
> so the site looks fine in your browser while the reviewer sees a login wall and Vapi's
> webhook receives a 302 instead of your API. Always verify from outside the browser:
>
> ```bash
> curl -sS https://your-app.vercel.app/health
> ```
>
> If that returns HTML instead of JSON, protection is still on.
</details>

<details>
<summary><b>3. Voice agent — Vapi</b></summary>

```bash
export PUBLIC_BASE_URL='https://your-app.vercel.app'
export VAPI_SERVER_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
export VAPI_API_KEY='your-vapi-private-key'

python -m scripts.build_assistant --publish
```

This lifts the prompt out of `voice/system_prompt.md`, attaches all six tools to
`$PUBLIC_BASE_URL/api/vapi/tool`, creates the assistant, and then **reads it back** to
verify the tools and prompt actually persisted. Put the same `VAPI_SERVER_SECRET` in
Vercel's environment variables.

Finally, buy a number in the Vapi dashboard (Phone Numbers → Buy) and assign it to the
assistant.

> Re-running with `VAPI_ASSISTANT_ID` set updates in place. Note that Vapi's `PATCH`
> replaces nested objects wholesale — sending a partial `model` object silently wipes the
> tools and the system prompt — so this script always sends the complete object and
> verifies afterwards.
</details>

---

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Production | Postgres connection string. Also reads `POSTGRES_URL` / `POSTGRES_PRISMA_URL` / `NEON_DATABASE_URL`, since Vercel's integrations inject different names. **Falls back to local SQLite when unset.** |
| `VAPI_SERVER_SECRET` | Production | Shared secret Vapi sends as `X-Vapi-Secret`. Compared in constant time. Unset ⇒ verification skipped (local only). |
| `APP_ENV` | No | `development` / `production`. Cosmetic; surfaced on `/health`. |
| `LOG_LEVEL` | No | Default `INFO`. |
| `PUBLIC_BASE_URL` | Setup only | Used by `build_assistant.py` to point tools at the right webhook. |
| `VAPI_API_KEY` | Setup only | Vapi private key, for `--publish`. |
| `VAPI_ASSISTANT_ID` | Setup only | Update an existing assistant instead of creating one. |

No secret is ever read from source. `.env` is gitignored, and `build_assistant.py`
redacts the secret out of the JSON it writes.

---

## API

Every response uses the envelope `{"data": ..., "error": ...}`.

| Method | Endpoint | Notes |
| --- | --- | --- |
| `GET` | `/patients` | Filters: `?last_name=` (case-insensitive), `?date_of_birth=`, `?phone_number=`. Also `?include_deleted=`, `?limit=`, `?offset=`. |
| `GET` | `/patients/{id}` | `404` if missing or soft-deleted. |
| `POST` | `/patients` | `201` + `Location` header. |
| `PUT` | `/patients/{id}` | Partial — only fields present are written. |
| `DELETE` | `/patients/{id}` | **Soft delete**: stamps `deleted_at`. Second delete ⇒ `404`. |
| `POST` | `/api/vapi/tool` | Voice webhook. Requires `X-Vapi-Secret`. |
| `GET` | `/dashboard` | HTML. `GET /` redirects here. |
| `GET` | `/health` | Diagnosable — see below. |

**Status codes:** `200` ok · `201` created · `400` malformed JSON or bad filter value ·
`404` not found · `422` well-formed body that fails validation · `500` unexpected.

> FastAPI returns `422` for malformed JSON *and* for invalid values. The brief lists both
> `400` and `422`, so the handler in `main.py` inspects the error type and splits them.

### Filters accept anything a human would type

```bash
curl "$BASE/patients?phone_number=(512)%20555-0142"   # matches stored 5125550142
curl "$BASE/patients?date_of_birth=1985-03-05"        # or 03/05/1985
curl "$BASE/patients?last_name=doe"                   # case-insensitive
```

### Example

```bash
curl -X POST "$BASE/patients" -H 'Content-Type: application/json' -d '{
  "first_name":"Jane","last_name":"Doe","date_of_birth":"03/05/1985",
  "sex":"Female","phone_number":"(512) 555-0142",
  "address_line_1":"412 Oak Street","city":"Austin","state":"TX","zip_code":"78704"
}'
```

```json
{
  "data": {
    "patient_id": "5a7a3c6c-c5d8-4aed-8cd6-563680ce0127",
    "first_name": "Jane", "last_name": "Doe",
    "date_of_birth": "03/05/1985", "sex": "Female",
    "phone_number": "(512) 555-0142", "state": "TX",
    "preferred_language": "English",
    "created_at": "2026-09-09T18:01:33", "deleted_at": null
  },
  "error": null
}
```

---

## The voice agent

The system prompt and the reasoning behind every rule in it live in
**[`voice/system_prompt.md`](voice/system_prompt.md)**. `build_assistant.py` reads the
prompt out of that file, so the documented version and the deployed version cannot drift.

### Tools

| Tool | When | Why it exists |
| --- | --- | --- |
| `lookup_patient` | Silently at connect | Duplicate detection by caller ID. |
| `save_progress` | After each group of answers | Checkpoints a partial registration. |
| `confirm_details` | Once, before the read-back | Validates everything and returns the sentence to read aloud. |
| `register_patient` | After explicit confirmation | The write. Idempotent on `call_id`. |
| `update_patient` | Returning caller | Partial update. |
| `offer_appointment` | After a save | Mock scheduling (bonus). |

### Three design choices worth calling out

**1. Tool results are spoken instructions, not status codes.** The model reads our
response as context for its next utterance, so we tell it what to *say*:

```
VALIDATION_ERROR field=date_of_birth: The date of birth cannot be in the future.
The record was NOT saved. Do NOT re-read the whole summary. Apologize briefly, ask
the caller only for that one field, then call register_patient again with the full
set of values including the correction.
```

One convention covers three graded edge cases at once: an invalid field re-prompts for
*that field alone*, a database failure produces a spoken apology instead of silence, and
the agent never has to improvise an error-handling policy mid-call.

**2. Speech normalization is code, not prompt.** Prompt rules do not reliably repair
transcription artifacts, so this is deterministic and unit-tested:

| Caller does | Transcriber emits | Stored |
| --- | --- | --- |
| spells a name | `D-A-V-I-S` | `Davis` |
| has a hyphenated name | `Smith-Jones` | `Smith-Jones` *(not joined)* |
| says a date | `March 5 1985` | `1985-03-05` |
| says a state | `California` | `CA` |
| dictates a ZIP | `941051234` | `94105-1234` |
| gives a phone | `+1 (415) 555-0192` | `4155550192` |
| dictates an email | `jane dot doe at gmail dot com` | `jane.doe@gmail.com` |
| answers sex | `f` / `prefer not to say` | `Female` / `Decline to Answer` |

Two subtleties: hyphen-joining only fires when *every* chunk is a single character, so
`Smith-Jones` survives; and re-casing only touches words with no case information of
their own, so `McDonald` does not become `Mcdonald`.

**3. The confirmation read-back is generated server-side.** `confirm_details` returns the
exact sentence to speak, with the date rendered `March 5th, 1985`, the phone grouped as
`5 1 2, 5 5 5, 0 1 4 2`, and the state spoken in full. A model asked to "read the number
back clearly" will occasionally say "four billion one hundred fifty five million…". This
also moves validation *before* the confirmation, so a bad date of birth is caught at the
natural moment to re-ask rather than after the caller has already said yes.

---

## Edge cases and resilience

| Scenario | Behaviour |
| --- | --- |
| **Invalid date of birth** | Caught by `confirm_details` *before* the read-back. Agent re-prompts for that field only. |
| **3-digit phone number** | Rejected with a spoken explanation; only that field is re-asked. |
| **Database write fails** | Webhook returns `SAVE_FAILED` with instructions to apologize and offer a retry. The caller never hears silence, and is never told they are registered when they are not. |
| **Call drops mid-conversation** | `save_progress` checkpoints after every group, so partial data is on disk within a second of being spoken. Abandoned drafts appear on the dashboard as a follow-up queue. |
| **Caller wants to start over** | Explicit instruction in the prompt: discard everything, acknowledge, restart from the first name. |
| **Model fires the same tool twice** | `create_patient` treats `call_id` as an idempotency key and returns the existing record instead of inserting a duplicate. |
| **Any unhandled exception in a tool** | Caught and converted to a speakable apology. |
| **Any webhook error at all** | Always returns HTTP 200 (except a bad secret). A 5xx makes Vapi retry, and a retry mid-call is worse than a dropped event. |
| **Lookup fails** | Degrades silently to a normal new registration rather than blocking the call. |
| **Caller speaks Spanish** | Prompt switches language and records `preferred_language`. |
| **Misconfigured deploy** | `/health` answers even when the database is unreachable, and reports which env vars are set (booleans only, never values), so the broken piece is named rather than guessed. |

---

## Observability

Structured events go to stdout, which Vercel captures automatically:

```
patient.created {"event":"patient.created","source":"voice","patient_id":"…","name":"Jane Doe",…}
voice.registration_complete {"patient_id":"…","first_name":"Jane",…}     ← full payload
vapi.handled {"type":"tool-calls","call_id":"…","ms":41.2}               ← webhook latency
draft.saved {"call_id":"…","fields":["first_name","last_name"]}
call.record_saved {"call_id":"…","patient_id":"…","has_transcript":true}
```

Webhook handling time is logged on every call because the caller experiences it as
silence. Locally, tool handlers complete in **~5–40 ms**; the dominant cost in production
is the Neon round trip, not our code.

> This deliberately logs PHI-shaped data because the brief asks for the collected payload
> to be observable. A production build would redact it or route it to an audit sink.

---

## Data model

19 columns across three tables. `patients` carries the demographics; `registration_drafts`
holds mid-call partials; `call_transcripts` holds the per-call audit record.

Notable schema decisions:

- **`patient_id` is a string UUID**, not a native Postgres `uuid`, so the identical schema
  runs on SQLite and Postgres — local tests exercise the production constraints.
- **CHECK constraints** on `sex`, phone length, state length, ZIP length and name lengths.
  Two write paths exist, so the database is where correctness is finally guaranteed.
- **"Not in the future" is deliberately *not* a CHECK constraint.** `CURRENT_DATE` is
  non-immutable, so Postgres re-evaluates it during `pg_dump`/restore and a valid old row
  can fail to reload. That rule lives in `validators.parse_dob()`, which both write paths
  share. Only a static `>= 1900-01-01` floor is enforced in SQL.
- **Phone stored as bare 10 digits**, formatted at the edges. Duplicate detection becomes
  an indexed equality match instead of fuzzy string comparison.
- **Indexes** on `phone_number` (hit on every inbound call), `last_name`,
  `date_of_birth`, and `registration_call_id`.
- **`registration_drafts` is a separate table holding JSON**, not an `in_progress` patient
  row — precisely so the `patients` NOT NULL and CHECK constraints stay strict. A draft is
  incomplete by definition; a patient never is.

---

## Testing

**77 tests, ~3 seconds**, no external services required.

```bash
pytest -q
```

| File | Covers |
| --- | --- |
| `test_validators.py` | Speech normalization: spelled names vs. hyphenated names, `McDonald` casing, 5 date formats, future/implausible DOB, phone formats, state names, ZIP+4, spoken email, read-back formatting, and an explicit **date-of-birth timezone round-trip** test. |
| `test_api.py` | All 5 endpoints, envelope shape, 200/201/400/404/422, partial update, revalidation on update, soft delete + hidden-from-reads + second-delete-404, filters in every accepted format. |
| `test_voice_flow.py` | **A conversation simulator** — replays realistic tool-call sequences through the live webhook. |

The conversation simulator is the closest thing to an automated test of the voice
experience. It cannot judge whether the agent *sounds* good, but it proves the entire
tool contract holds, including recovery paths that are awkward to trigger deliberately
while on the phone:

- a full happy-path registration, then reading the record back through the REST API
- the `D-A-V-I-S` correction, verifying `Davis` is what lands in the database
- a future date of birth → re-prompt for that field only
- a dropped call → recoverable draft, visible on the dashboard
- the same tool fired twice → one patient, not two
- a returning caller → recognized, then updated rather than duplicated
- both of Vapi's tool-call payload shapes, including arguments as a JSON string
- LLM placeholder junk (`"N/A"`, `"null"`, `""`) treated as absent
- the end-of-call report linking a transcript to a patient across two separate webhooks

---

## Bonus challenges

| Bonus | Status |
| --- | --- |
| Duplicate detection | ✅ `lookup_patient` on caller ID; offers update instead of create |
| Appointment scheduling | ✅ `offer_appointment` with mock slots |
| Multi-language | ✅ Prompt switches to Spanish; `preferred_language` recorded |
| Call transcript | ✅ Transcript, summary, recording URL and duration stored per call, linked to the patient |
| Dashboard | ✅ Patients, recent calls, and abandoned registrations |
| Automated tests | ✅ 77 tests incl. the conversation simulator |

---

## Known limitations and trade-offs

Stated plainly, because pretending they do not exist is worse than having them.

1. **Duplicate detection is phone-only.** Family members sharing a landline collide.
   Matching on name + date of birth as a secondary key is the right fix.
2. **`PUT` cannot null out an optional field.** Fields absent from the request are left
   untouched, so there is no way to *clear* an email. A `PATCH` with explicit `null`
   semantics, or a `?clear=` parameter, would resolve it.
3. **Appointments are not persisted.** `offer_appointment` returns fixed mock slots and
   stores nothing. The brief permits mock data, and a half-real appointments table would
   be more misleading than an honest stub.
4. **Schema is created via `create_all`, not migrations.** Fine for one table set and zero
   manual deploy steps; a real project needs Alembic before the second schema change.
5. **The API is unauthenticated.** Deliberate — the reviewer needs to `curl` it freely.
   `ADMIN_API_KEY` exists in config as the hook for bearer auth on mutating routes.
6. **No rate limiting.** A public write endpoint should have it.
7. **Cold starts.** The first request after idle pays Vercel's cold start plus Neon's wake,
   which can add ~1–2s to the very first tool call of the day. A keep-warm ping would
   remove it.
8. **PHI-shaped data is logged in the clear.** Required by the brief's observability
   section; not acceptable in production.
9. **Spanish is prompt-level only.** The transcriber stays pinned to `en`, so a fully
   Spanish call will transcribe worse than an English one. Proper support needs a
   language-detection switch on the transcriber.

---

## Next steps

In the order I would actually do them:

1. **Keep-warm ping** — cheapest possible fix for the worst latency, at the worst moment.
2. **Resolve the caller before the call is answered.** Vapi's `assistant-request` webhook
   can return a per-call assistant, so the lookup happens server-side *before* the agent
   speaks. That removes the first tool round-trip entirely and enables a genuinely
   personalised opener — *"Hi Jane, welcome back. Calling to update your information?"* —
   instead of a generic greeting followed by a pause.
3. **Alembic migrations.**
4. **Bearer auth + rate limiting** on the mutating endpoints.
5. **Name + DOB as a secondary duplicate key.**
6. **Transcript detail view** on the dashboard, with inline audio playback.
7. **Latency percentiles** published from the existing per-call timing logs.

---

## Assessment requirement coverage

<details>
<summary>Click to expand</summary>

| Requirement | Where |
| --- | --- |
| Real dialable US number | Vapi — see Live demo |
| Natural conversation, not IVR | `voice/system_prompt.md` |
| LLM-powered, handles varied phrasing + corrections | GPT-4o + `validators.normalize_spoken_name` |
| Read back all info before saving | `confirm_details` tool, server-generated sentence |
| Re-prompt on invalid data | `VALIDATION_ERROR` instruction naming a single field |
| Graceful call completion | Prompt "ENDING THE CALL" + `endCallPhrases` |
| All 16 demographic fields + 3 auto | `app/models.py` |
| Optional fields opt-in | Prompt "WHAT YOU COLLECT" |
| Persistent DB surviving restarts | Neon Postgres |
| Schema with types + constraints | `app/models.py` — CHECK constraints, indexes |
| Seed data | `scripts/seed.py` |
| 5 REST endpoints + filters | `app/routers/patients.py` |
| Status codes 200/201/400/404/422/500 | `app/main.py` exception handlers |
| Server-side validation | `app/schemas.py` + `app/validators.py` |
| `{data, error}` envelope | `app/schemas.py::Envelope` |
| Agent writes via the service layer | `routers/vapi.py` → `services/patients.py` |
| Outcome relayed to caller | `SAVED` / `SAVE_FAILED` tool results |
| No hardcoded secrets | `app/config.py`, `.env.example` |
| Input sanitization | `validators.clean_text` strips control chars |
| Conversation logging to stdout | `services/_log_payload`, `voice.registration_complete` |

</details>
