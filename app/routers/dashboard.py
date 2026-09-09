"""Minimal server-rendered dashboard.

Deliberately dependency-free HTML rather than a SPA: it has to survive a cold start on
Vercel's Python runtime with no build step, and the reviewer just needs to see that
records land in the database after a call.
"""
import html
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CallTranscript
from app.services import patients as service
from app.validators import ValidationProblem, format_dob, format_phone

router = APIRouter(tags=["dashboard"])

_STYLE = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#12172b;--muted:#5b6478;--line:#e3e7ee;
--accent:#2f5bea;--accent-soft:#eaf0ff;--ok:#0f7b4f;--ok-soft:#e6f5ee}
@media (prefers-color-scheme:dark){:root{--bg:#0e1220;--panel:#161c2e;--ink:#eef1f8;
--muted:#96a0b8;--line:#27304a;--accent:#7f9bff;--accent-soft:#1d2745;--ok:#4ade80;
--ok-soft:#14301f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 64px}
header{display:flex;flex-wrap:wrap;gap:16px;align-items:baseline;justify-content:space-between}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 18px;min-width:150px}
.stat b{display:block;font-size:26px;letter-spacing:-.02em}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
form{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
input{background:var(--panel);border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:9px 12px;font-size:14px;min-width:190px}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 18px;
font-size:14px;font-weight:600;cursor:pointer}
a.clear{align-self:center;color:var(--muted);font-size:13px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;
overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:900px}
th{text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);padding:12px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.name{font-weight:600}
.uuid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--muted)}
.tag{display:inline-block;background:var(--accent-soft);color:var(--accent);
border-radius:99px;padding:2px 9px;font-size:11px;font-weight:600}
.tag.warn{background:#fdf0e3;color:#9a5b12}
@media (prefers-color-scheme:dark){.tag.warn{background:#3a2a12;color:#f0b46a}}
a{color:var(--accent)}
.empty{padding:48px 20px;text-align:center;color:var(--muted)}
h2{font-size:15px;margin:34px 0 12px}
footer{margin-top:32px;color:var(--muted);font-size:12px}
code{background:var(--accent-soft);color:var(--accent);padding:1px 6px;border-radius:5px;
font-size:12px}
"""


def _esc(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "&mdash;"


def _patient_row(p) -> str:
    optional_bits = []
    if p.insurance_provider:
        optional_bits.append(f"Ins: {_esc(p.insurance_provider)}")
    if p.emergency_contact_name:
        optional_bits.append(f"EC: {_esc(p.emergency_contact_name)}")
    if p.preferred_language and p.preferred_language != "English":
        optional_bits.append(f"Lang: {_esc(p.preferred_language)}")
    extras = " &middot; ".join(optional_bits) or "&mdash;"

    addr = _esc(p.address_line_1)
    if p.address_line_2:
        addr += f", {_esc(p.address_line_2)}"
    addr += f"<br><span class='uuid'>{_esc(p.city)}, {_esc(p.state)} {_esc(p.zip_code)}</span>"

    return f"""<tr>
<td><div class="name">{_esc(p.first_name)} {_esc(p.last_name)}</div>
<div class="uuid">{_esc(p.patient_id)}</div></td>
<td>{_esc(format_dob(p.date_of_birth))}</td>
<td><span class="tag">{_esc(p.sex)}</span></td>
<td>{_esc(format_phone(p.phone_number))}<br><span class="uuid">{_esc(p.email)}</span></td>
<td>{addr}</td>
<td>{extras}</td>
<td class="uuid">{_esc(p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else None)} UTC</td>
</tr>"""


def _recording_link(url) -> str:
    """Recording URLs are provider-hosted; render as a link rather than an audio tag
    so a expired or missing URL degrades to a dead link instead of a broken player."""
    if not url:
        return "&mdash;"
    return f'<a href="{html.escape(str(url))}" target="_blank" rel="noopener">Listen</a>'


def _draft_row(d) -> str:
    """An abandoned draft is a follow-up task, so show what was captured before the drop."""
    try:
        fields = json.loads(d.payload or "{}")
    except json.JSONDecodeError:
        fields = {}
    name = " ".join(
        str(fields[k]) for k in ("first_name", "last_name") if fields.get(k)
    )
    captured = ", ".join(sorted(k for k, val in fields.items() if val))
    return (
        f"<tr><td class='name'>{_esc(name) if name else '&mdash;'}</td>"
        f"<td>{_esc(format_phone(d.caller_phone) or d.caller_phone)}</td>"
        f"<td><span class='tag warn'>{len([1 for val in fields.values() if val])} fields</span></td>"
        f"<td class='uuid'>{_esc(captured)}</td>"
        f"<td class='uuid'>{_esc(d.updated_at.strftime('%Y-%m-%d %H:%M') if d.updated_at else None)} UTC</td>"
        "</tr>"
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    q_last_name: Optional[str] = Query(None, alias="last_name"),
    q_phone: Optional[str] = Query(None, alias="phone_number"),
    db: Session = Depends(get_db),
):
    """Read-only view of everything the voice agent has registered."""
    error = None
    try:
        rows = service.list_patients(
            db, last_name=q_last_name, phone_number=q_phone, limit=200
        )
    except ValidationProblem as exc:
        error, rows = exc.message, []

    total = len(service.list_patients(db, limit=500))
    calls = list(
        db.execute(
            select(CallTranscript).order_by(desc(CallTranscript.created_at)).limit(8)
        )
        .scalars()
        .all()
    )
    linked = sum(1 for c in calls if c.patient_id)
    drafts = service.list_abandoned_drafts(db, limit=10)

    if rows:
        body = (
            "<table><thead><tr><th>Patient</th><th>DOB</th><th>Sex</th>"
            "<th>Contact</th><th>Address</th><th>Optional</th><th>Registered</th>"
            "</tr></thead><tbody>"
            + "".join(_patient_row(p) for p in rows)
            + "</tbody></table>"
        )
    else:
        msg = _esc(error) if error else "No patients yet &mdash; call the number to register one."
        body = f"<div class='empty'>{msg}</div>"

    if calls:
        call_rows = "".join(
            f"<tr><td class='uuid'>{_esc(c.call_id)}</td>"
            f"<td>{_esc(format_phone(c.caller_phone) or c.caller_phone)}</td>"
            f"<td>{_esc(c.ended_reason)}</td>"
            f"<td class='uuid'>{_esc(c.patient_id)}</td>"
            f"<td>{_recording_link(c.recording_url)}</td>"
            f"<td>{_esc((c.summary or '')[:200])}</td></tr>"
            for c in calls
        )
        calls_html = (
            "<div class='panel'><table><thead><tr><th>Call ID</th><th>Caller</th>"
            "<th>Ended</th><th>Patient</th><th>Audio</th><th>Summary</th>"
            "</tr></thead><tbody>"
            + call_rows
            + "</tbody></table></div>"
        )
    else:
        calls_html = "<div class='panel'><div class='empty'>No call records yet.</div></div>"

    if drafts:
        drafts_html = (
            "<div class='panel'><table><thead><tr><th>Name so far</th><th>Caller</th>"
            "<th>Progress</th><th>Fields captured</th><th>Last update</th>"
            "</tr></thead><tbody>"
            + "".join(_draft_row(d) for d in drafts)
            + "</tbody></table></div>"
        )
    else:
        drafts_html = (
            "<div class='panel'><div class='empty'>No incomplete registrations.</div></div>"
        )

    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Patient Registration Dashboard</title><style>{_STYLE}</style></head>
<body><div class="wrap">
<header>
  <div><h1>Patient Registration Dashboard</h1>
  <div class="sub">Records collected by the voice AI intake agent</div></div>
  <div class="sub"><a href="/call"><b>&#127908; Talk to the agent in your browser</b></a><br>API docs: <code>/docs</code> &middot; JSON: <code>/patients</code></div>
</header>

<div class="stats">
  <div class="stat"><span>Active patients</span><b>{total}</b></div>
  <div class="stat"><span>Showing</span><b>{len(rows)}</b></div>
  <div class="stat"><span>Recent calls</span><b>{len(calls)}</b></div>
  <div class="stat"><span>Calls w/ record</span><b>{linked}</b></div>
  <div class="stat"><span>Incomplete calls</span><b>{len(drafts)}</b></div>
</div>

<form method="get" action="/dashboard">
  <input name="last_name" placeholder="Last name" value="{_esc(q_last_name) if q_last_name else ''}">
  <input name="phone_number" placeholder="Phone number" value="{_esc(q_phone) if q_phone else ''}">
  <button type="submit">Search</button>
  <a class="clear" href="/dashboard">Reset</a>
</form>

<div class="panel">{body}</div>

<h2>Recent calls</h2>
{calls_html}

<h2>Incomplete registrations</h2>
<div class="sub" style="margin-bottom:10px">Calls that ended before the caller confirmed.
Partial data is checkpointed during the call so nothing spoken is lost.</div>
{drafts_html}

<footer>Soft-deleted records are hidden. Add <code>?include_deleted=true</code> to
<code>/patients</code> to see them.</footer>
</div></body></html>""")
