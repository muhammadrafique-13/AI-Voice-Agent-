"""Browser-based voice call, using the same assistant as the phone number.

Why this exists: the phone number is US-only, so anyone outside the US (or without
international calling) cannot try the agent. This page opens a WebRTC call to the exact
same Vapi assistant, which means the same system prompt, the same six tools, the same
webhook and the same database - only the transport differs.

The Vapi *public* key is designed to be embedded in client-side code; it can only start
calls against assistants in the account and carries no admin rights. The private key
never leaves the server.
"""
import html as _html
import json
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])


def _first_line(value: str) -> str:
    """Take the first non-empty line of an environment value.

    Values pasted into a hosting dashboard routinely arrive with trailing newlines, or
    with the same value repeated across the three environment boxes and concatenated.
    A credential is always a single line, so anything after the first is noise.
    """
    for line in (value or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""

# Read from the environment rather than hardcoded, even though both values are safe to
# render into a public page (the *public* key is a browser credential by design, and an
# assistant id is not a secret). Keeping them out of source means the repository holds no
# vendor identifiers at all, and a fork points at its own account by changing config.
VAPI_PUBLIC_KEY = _first_line(os.getenv("VAPI_PUBLIC_KEY", ""))
VAPI_ASSISTANT_ID = _first_line(os.getenv("VAPI_ASSISTANT_ID", ""))
PHONE_NUMBER = _first_line(os.getenv("PUBLIC_PHONE_NUMBER", "")) or "the number in the README"

_NOT_CONFIGURED = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Web call not configured</title>
<div style="font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
max-width:620px;margin:60px auto;padding:0 20px">
<h1 style="font-size:20px">Browser calling is not configured</h1>
<p>Set <code>VAPI_PUBLIC_KEY</code> and <code>VAPI_ASSISTANT_ID</code> in the environment
to enable this page. The phone line and the REST API are unaffected.</p>
<p><a href="/dashboard">Back to the dashboard</a></p></div>"""

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Talk to the Intake Agent</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#12172b;--muted:#5b6478;--line:#e3e7ee;
--accent:#2f5bea;--accent-soft:#eaf0ff;--ok:#0f7b4f;--ok-soft:#e6f5ee}
@media (prefers-color-scheme:dark){:root{--bg:#0e1220;--panel:#161c2e;--ink:#eef1f8;
--muted:#96a0b8;--line:#27304a;--accent:#7f9bff;--accent-soft:#1d2745;--ok:#4ade80;
--ok-soft:#14301f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:40px 20px 64px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.015em}
.sub{color:var(--muted);font-size:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:28px;margin:26px 0;text-align:center}
#orb{width:104px;height:104px;border-radius:50%;margin:0 auto 20px;
background:var(--accent-soft);display:flex;align-items:center;justify-content:center;
font-size:40px;transition:transform .18s ease, box-shadow .18s ease}
#orb.live{box-shadow:0 0 0 10px var(--accent-soft)}
#orb.talking{transform:scale(1.09);box-shadow:0 0 0 16px var(--accent-soft)}
button{border:0;border-radius:10px;padding:13px 30px;font-size:15px;font-weight:600;
cursor:pointer;font-family:inherit}
#start{background:var(--accent);color:#fff}
#stop{background:#d92d47;color:#fff}
button:disabled{opacity:.5;cursor:not-allowed}
#status{margin-top:16px;color:var(--muted);font-size:14px;min-height:22px}
.hint{background:var(--accent-soft);border-radius:10px;padding:14px 18px;
font-size:13.5px;color:var(--ink);text-align:left;margin-top:22px}
.hint b{display:block;margin-bottom:6px}
#log{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:0;max-height:340px;overflow-y:auto;display:none}
.turn{padding:11px 18px;border-bottom:1px solid var(--line);font-size:14px}
.turn:last-child{border-bottom:0}
.turn .who{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted);display:block;margin-bottom:2px}
.turn.agent .who{color:var(--accent)}
h2{font-size:14px;margin:30px 0 10px;color:var(--muted);text-transform:uppercase;
letter-spacing:.06em}
a{color:var(--accent)}
.alt{font-size:13.5px;color:var(--muted);margin-top:22px;text-align:center}
.alt b{color:var(--ink)}
ul{margin:8px 0 0;padding-left:20px}li{margin:3px 0}
</style></head>
<body><div class="wrap">

<h1>Talk to the intake agent</h1>
<div class="sub">Same assistant, same database as the phone line &mdash; running over your
browser microphone.</div>

<div class="card">
  <div id="orb">&#127908;</div>
  <button id="start">Start call</button>
  <button id="stop" style="display:none">End call</button>
  <div id="status">Click to start. Your browser will ask for microphone access.</div>

  <div class="hint">
    <b>Try saying:</b>
    "Hi, I'd like to register as a new patient. My name is Sarah Chen, S-A-R-A-H,
    C-H-E-N. I was born June 14th, 1992."
    <ul>
      <li>Correct yourself mid-call &mdash; it should accept it without restarting</li>
      <li>Give a 3-digit phone number &mdash; it should re-ask for just that</li>
      <li>Say a birthday in 2099 &mdash; it should catch it</li>
      <li>Say "can we start over?" &mdash; it should reset cleanly</li>
    </ul>
  </div>
</div>

<h2>Live transcript</h2>
<div id="log"></div>

<div class="alt">
  Prefer the phone? Call <b>__PHONE__</b> (US).<br>
  Records appear on the <a href="/dashboard">dashboard</a> as soon as a call completes.
</div>

</div>
<script type="module">
import * as VapiModule from "https://cdn.jsdelivr.net/npm/@vapi-ai/web@2.7.0/+esm";

// jsDelivr's ESM transform of this CommonJS package double-wraps the export: the
// module's `default` is the whole module.exports object, so the constructor actually
// sits at `.default.default`. Resolve by looking for the first candidate that is
// callable, which survives either packaging shape.
function resolveVapi(mod) {
  const candidates = [
    mod && mod.default && mod.default.default,
    mod && mod.default,
    mod && mod.Vapi,
    mod,
  ];
  return candidates.find((c) => typeof c === "function");
}

const PUBLIC_KEY = __PUBLIC_KEY__;
const ASSISTANT  = __ASSISTANT_ID__;

const orb    = document.getElementById("orb");
const start  = document.getElementById("start");
const stop   = document.getElementById("stop");
const status = document.getElementById("status");
const log    = document.getElementById("log");

function setStatus(text) { status.textContent = text; }

function addTurn(who, text) {
  log.style.display = "block";
  const el = document.createElement("div");
  el.className = "turn " + (who === "Agent" ? "agent" : "you");
  el.innerHTML = '<span class="who"></span><span class="body"></span>';
  el.querySelector(".who").textContent = who;
  el.querySelector(".body").textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

function resetControls() {
  orb.classList.remove("live", "talking");
  stop.style.display = "none";
  start.style.display = "inline-block";
  start.disabled = false;
}

let vapi = null;
const Vapi = resolveVapi(VapiModule);

if (typeof Vapi !== "function") {
  start.disabled = true;
  setStatus("Voice client failed to load. Use the phone number instead.");
} else {
  try {
    vapi = new Vapi(PUBLIC_KEY);
  } catch (err) {
    start.disabled = true;
    setStatus("Could not initialise the voice client: " + (err && err.message ? err.message : err));
  }
}

if (vapi) {
  start.addEventListener("click", async () => {
    start.disabled = true;
    setStatus("Connecting… allow microphone access if prompted.");
    try {
      await vapi.start(ASSISTANT);
    } catch (err) {
      resetControls();
      setStatus("Could not start the call: " + (err && err.message ? err.message : err));
    }
  });

  stop.addEventListener("click", () => vapi.stop());

  vapi.on("call-start", () => {
    orb.classList.add("live");
    start.style.display = "none";
    stop.style.display = "inline-block";
    setStatus("Connected — say hello.");
  });

  vapi.on("call-end", () => {
    resetControls();
    setStatus("Call ended. Check the dashboard for the saved record.");
  });

  // Fires while the assistant is speaking - drives the orb animation.
  vapi.on("speech-start", () => orb.classList.add("talking"));
  vapi.on("speech-end",   () => orb.classList.remove("talking"));

  vapi.on("message", (msg) => {
    if (msg.type === "transcript" && msg.transcriptType === "final") {
      addTurn(msg.role === "assistant" ? "Agent" : "You", msg.transcript);
    }
  });

  vapi.on("error", (err) => {
    console.error(err);
    const detail = (err && (err.errorMsg || err.message)) || "";
    resetControls();
    setStatus("Error: " + (detail || "the call could not continue."));
  });
}
</script>
</body></html>
"""


@router.get("/call", response_class=HTMLResponse)
def web_call():
    """Browser voice client for the same assistant the phone number uses."""
    if not (VAPI_PUBLIC_KEY and VAPI_ASSISTANT_ID):
        return HTMLResponse(_NOT_CONFIGURED, status_code=503)

    html = (
        # json.dumps supplies the quotes and escapes anything unexpected.
        _PAGE.replace("__PUBLIC_KEY__", json.dumps(VAPI_PUBLIC_KEY))
        .replace("__ASSISTANT_ID__", json.dumps(VAPI_ASSISTANT_ID))
        .replace("__PHONE__", _html.escape(PHONE_NUMBER))
    )
    return HTMLResponse(html)
