"""
PlacidWay Provider Onboarding - WhatsApp chat SIMULATOR (Streamlit client demo)
==============================================================================

Run:   pip install streamlit
       streamlit run app.py

What this is
------------
A self-contained demo that LOOKS like WhatsApp and BEHAVES like the planned n8n chatbot:

  Inbound Webhook -> Normalize Message -> Input/STOP check -> Status API (MOCK)
  -> Validate -> Find First Incomplete Step -> Route by Command -> Build Reply
  -> Twilio Send (MOCK) -> Log

Key rule shown to the client (Option A - "Guide and Verify"):
  * The provider completes forms on the PlacidWay portal (simulated with a sidebar button).
  * The bot NEVER trusts "DONE". Every message triggers a fresh check against the (mock) PlacidWay
    status API, which is the single source of truth.

Everything here is MOCK/DEMO: no WhatsApp, Twilio, PlacidWay API or AI service is called.
FAQ wording is taken from the client's Scope of Work and is pending final client approval.

File layout (single file, top to bottom):
  1. Constants & onboarding step definitions
  2. Mock PlacidWay status API
  3. Bot engine (pure Python, no Streamlit dependency -> easy to test)
  4. Streamlit UI (WhatsApp-style chat + demo controls + "under the hood" panels)
"""
from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# =============================================================================
# 1. CONSTANTS & STEP DEFINITIONS
# =============================================================================

PORTAL_BASE_URL = "http://www.placidway.com"   # documented portal paths; links are for demonstration only
ACTIVATION_LINK_MOCK = "https://example.invalid/activate?token=MOCK"
PROVIDER_PHONE = "+15550100003"
COMMANDS = ("START", "CONTINUE", "STATUS", "HELP", "DONE", "SIGNED", "SUPPORT", "SALES", "PAUSE", "STOP")
COMMANDS_LINE = "START, CONTINUE, STATUS, HELP, DONE, SUPPORT, SALES, PAUSE, STOP"
MAX_MESSAGE_LEN = 1000
TOTAL_SECTIONS = 5
VALID_STEP_STATUS = ("NOT_STARTED", "IN_PROGRESS", "COMPLETED")
VALID_ACCOUNT_STATUS = ("ACTIVE", "PENDING_ACTIVATION", "INACTIVE")


@dataclass(frozen=True)
class Step:
    stage: int                      # 0 = account activation, 1..5 = application sections
    key: str
    label: str
    path: Optional[str]             # portal path (None for activation)
    intro: str                      # sentence shown before the link
    finish_hint: str                # how the provider reports completion
    help_text: str                  # answer for HELP while on this step
    lead: str = ""                  # optional line before the step header
    sample_missing: tuple = ()      # fields shown as missing when a step is "started but incomplete"
    faq: tuple = ()                 # (regex, answer) pairs - step-specific approved answers


STEPS = (
    Step(0, "account_activation", "Account Activation", None,
         "Please activate your account here:",
         "Reply **DONE** once you've activated it and I'll verify it.",
         "Use the activation link to activate your PlacidWay provider account. Activation comes before the 5 application steps.",
         faq=((r"(activate|activation|link|email)",
               "Open the activation link and follow the prompts, then reply **DONE** so I can verify your account."),)),
    Step(1, "center_details", "Center Details", "/provider-portal/agreement/center-details",
         "Complete your center information here:",
         "Come back and reply **DONE** when finished.",
         "You'll need basic information about your center, such as:\n• Center name\n• Address\n• Contact details\n• Website\n• Business information",
         sample_missing=("address", "website"),
         faq=((r"(information|info|details|fields?|fill|complete|need)",
               "You'll need basic information about your center, such as:\n• Center name\n• Address\n• Contact details\n• Website\n• Business information"),)),
    Step(2, "certificates", "Licenses & Certificates", "/provider-portal/agreement/center-certificates",
         "Upload your center licenses and certificates here:",
         "Reply **DONE** when finished.",
         "Upload the documents that apply to your center, such as:\n• Medical facility license\n• Registration documents\n• Accreditation\n• Healthcare certificates\nYou can upload clear photos or files from your phone if supported by the form.",
         sample_missing=("business_license",),
         faq=((r"(don'?t|do not|dont|haven'?t|no).{0,25}(have|document|licen[cs]e|certificate)",
               "Upload the documents you currently have.\nIf you're unsure whether a document is required, reply **SUPPORT** and we can connect you with the PlacidWay team."),
              (r"(upload|document|certificate|licen[cs]e|accredit|file|photo)",
               "Upload the documents that apply to your center, such as:\n• Medical facility license\n• Registration documents\n• Accreditation\n• Healthcare certificates\nYou can upload clear photos or files from your phone if supported by the form."))),
    Step(3, "center_credentials", "Center Credentials", "/provider-portal/agreement/center-credentials",
         "Add your treatments, specialties, social media and other center information here:",
         "Reply **DONE** when finished.",
         "Add your treatments, specialties, social media and other center information, then save.",
         sample_missing=("treatments",),
         faq=((r"(social|facebook|instagram|linkedin|links?)",
               "That's okay.\nComplete the information you currently have. If a field is optional, you can continue without it."),
              (r"(treatment|specialt|credential|profile)",
               "Add your treatments, specialties, social media and other center information, then save."))),
    Step(4, "marketing_package", "Marketing Package", "/provider-portal/agreement/center-marketing",
         "Review the available PlacidWay packages and select the one that works for your center:",
         "Reply **DONE** after selecting your package.",
         "Review the packages on the page and select the one that works for your center. I can't choose or recommend a package for you.",
         sample_missing=("package selection",),
         faq=((r"(which|best|recommend|choose|select|compare|package|plan)",
               "The best option depends on your center's goals and the services you need.\nYou can compare the available packages on the page.\nIf you'd like someone to explain the options, reply **SALES**."),)),
    Step(5, "provider_agreement", "Provider Agreement", "/provider-portal/agreement/center-agreement",
         "Review and sign your agreement here:",
         "Reply **SIGNED** when finished.",
         "Review and electronically sign your agreement on the page. It should be signed by someone authorized to represent your organization.",
         lead="One last step.",
         sample_missing=("signature",),
         faq=((r"(who|sign|signature|authori[sz]ed)",
               "It should be signed by someone authorized to represent your organization.\nIf you're unsure, reply **SUPPORT**."),)),
)
STEP_BY_KEY = {s.key: s for s in STEPS}
SECTION_STEPS = STEPS[1:]

# General approved FAQ (checked after step-specific blocked topics)
GENERAL_FAQ = (
    (r"(later|tomorrow|pause|come back|resume|save|busy|not now)",
     "Yes. Your progress will be saved.\nWhen you're ready, message me **CONTINUE** and I'll take you to the next incomplete step."),
    (r"how many (steps|sections)",
     "There are 5 application steps: Center Details, Licenses & Certificates, Center Credentials, Marketing Package and Provider Agreement."),
    (r"(approved|approval|accepted|guarantee|reviewed)",
     "I can only tell you which application sections are complete in PlacidWay's system - I can't confirm approval. Reply **SUPPORT** if you have questions about your application review."),
)
LEGAL_RE = re.compile(r"(legal|lawyer|attorney|liabilit|lawsuit|binding|sue\b)", re.I)
PRICING_RE = re.compile(r"(price|pricing|cost|fee\b|fees\b|discount|how much|payment)", re.I)
GREETING_RE = re.compile(r"^(hi|hello|hey|salam|assalam o alaikum|assalamu alaikum|good (morning|afternoon|evening))$", re.I)

# Pipeline nodes (names mirror the n8n workflow "PlacidWay Provider Onboarding Chatbot")
NODES = (
    "Incoming WhatsApp Webhook",
    "Normalize Incoming Message",
    "Input Valid? / STOP?",
    "Fetch Provider Status (Mock API)",
    "Validate API Response",
    "Find First Incomplete Step",
    "Route by Command",
    "Build Reply",
    "Simulate Twilio Send (MOCK)",
    "Log Interaction & Session",
)

# =============================================================================
# 2. MOCK PLACIDWAY STATUS API  (the real one does not exist yet; contract is PROPOSED / UNAPPROVED)
# =============================================================================

class APIError(Exception):
    """Raised when the (mock) status API is unavailable."""


class StatusError(Exception):
    """Raised when the API answers but the data is unusable (empty / unexpected values)."""


SCENARIOS = {
    "New provider (account not activated)": ("PENDING_ACTIVATION", "NNNNN"),
    "Active - nothing started": ("ACTIVE", "NNNNN"),
    "Partially completed (Step 1 done)": ("ACTIVE", "CNNNN"),
    "Almost there (Steps 1-4 done)": ("ACTIVE", "CCCCN"),
    "Fully completed": ("ACTIVE", "CCCCC"),
}
DEFAULT_SCENARIO = "Active - nothing started"
_CODE = {"N": "NOT_STARTED", "I": "IN_PROGRESS", "C": "COMPLETED"}


class MockPlacidWayAPI:
    """Stands in for the future PlacidWay status API. It is the demo's 'database': the bot only learns
    about progress by calling get_status(); chat messages never change it."""

    PROVIDER = {"provider_id": "P-1003", "name": "Blue Lagoon Wellness Center", "whatsapp_number": PROVIDER_PHONE}

    def __init__(self, scenario: str = DEFAULT_SCENARIO):
        self.api_down = False      # demo toggle: simulate an outage (HTTP 503)
        self.corrupt = False       # demo toggle: return an unexpected status value
        self.calls: list[dict] = []
        self.load_scenario(scenario)

    # ---- demo setup / "what the provider does in the portal" ----
    def load_scenario(self, name: str) -> None:
        account, codes = SCENARIOS[name]
        self.account_status = account
        self.steps = {s.key: {"status": _CODE[c], "missing_fields": []} for s, c in zip(SECTION_STEPS, codes)}
        for s in SECTION_STEPS:
            if self.steps[s.key]["status"] == "IN_PROGRESS":
                self.steps[s.key]["missing_fields"] = list(s.sample_missing)
        self.calls.clear()

    def _first_incomplete(self) -> Optional[Step]:
        if self.account_status != "ACTIVE":
            return STEPS[0]
        return next((s for s in SECTION_STEPS if self.steps[s.key]["status"] != "COMPLETED"), None)

    def complete_current_step(self) -> Optional[str]:
        """Simulate: the provider finished the current step IN THE PORTAL. Returns its label."""
        step = self._first_incomplete()
        if step is None:
            return None
        if step.stage == 0:
            self.account_status = "ACTIVE"
        else:
            self.steps[step.key] = {"status": "COMPLETED", "missing_fields": []}
        return step.label

    def start_current_step(self) -> Optional[str]:
        """Simulate: the provider opened the form, saved part of it, and left (IN_PROGRESS)."""
        step = self._first_incomplete()
        if step is None or step.stage == 0:
            return None
        self.steps[step.key] = {"status": "IN_PROGRESS", "missing_fields": list(step.sample_missing)}
        return step.label

    def snapshot(self) -> dict:
        """What the backend holds right now (no API call is logged; used only by the demo panels)."""
        body = {
            "success": True,
            "provider": {**self.PROVIDER, "account_status": self.account_status},
            "application": {"steps": copy.deepcopy(self.steps)},
            "_meta": {"mock": True, "contract": "PROPOSED - NOT APPROVED (pending PlacidWay dev team)"},
        }
        statuses = [v["status"] for v in self.steps.values()]
        body["application"]["overall_status"] = (
            "SUBMITTED" if self.account_status == "ACTIVE" and all(s == "COMPLETED" for s in statuses)
            else "NOT_STARTED" if all(s == "NOT_STARTED" for s in statuses) and self.account_status != "ACTIVE"
            else "IN_PROGRESS")
        first = self._first_incomplete()
        body["application"]["first_incomplete_step"] = first.key if first else None
        return body

    # ---- the one call the bot is allowed to make ----
    def get_status(self) -> dict:
        ts = datetime.now().strftime("%H:%M:%S")
        if self.api_down:
            self.calls.append({"time": ts, "request": f"POST /status {{whatsapp_number: {PROVIDER_PHONE}}}", "result": "503 SERVICE_UNAVAILABLE"})
            raise APIError("503 SERVICE_UNAVAILABLE (simulated outage)")
        body = self.snapshot()
        if self.corrupt:
            body["application"]["steps"]["certificates"]["status"] = "PENDING_REVIEW"   # value the contract does not allow
        self.calls.append({"time": ts, "request": f"POST /status {{whatsapp_number: {PROVIDER_PHONE}}}", "result": "200 OK"})
        self.calls[:] = self.calls[-30:]
        return body


# =============================================================================
# 3. BOT ENGINE  (pure Python; mirrors the n8n Main Chatbot workflow)
# =============================================================================

@dataclass
class Progress:
    """Result of 'Find First Incomplete Step'."""
    rows: list            # [{stage,key,label,status,missing_fields}]
    first: Optional[dict]  # first incomplete row, or None when everything is complete
    completed: int         # completed application sections (0..5), activation not counted
    provider_name: str
    overall_status: str


@dataclass
class Outcome:
    reply: str = ""
    note: str = ""                  # demo-only commentary shown as a gray pill (not a bot message)
    route: str = ""
    detail: str = ""
    state_update: dict = field(default_factory=dict)
    handoff: Optional[dict] = None
    suppress: bool = False          # bot stays silent (e.g. opted out)
    error_type: Optional[str] = None


@dataclass
class BotTurn:
    """Everything the UI needs after one inbound message."""
    reply: str
    note: str
    kind: str
    route: str
    trace: dict
    handoff: Optional[dict] = None
    error_type: Optional[str] = None


def new_trace() -> dict:
    return {n: {"status": "skipped", "detail": ""} for n in NODES}


def mark(trace: dict, node: str, status: str, detail: str = "") -> None:
    trace[node] = {"status": status, "detail": detail}


def looks_like_garbage(text: str) -> bool:
    """Heuristic for keyboard-mash / symbol-only input (kept simple on purpose; real words like Roman Urdu pass)."""
    if not re.search(r"[A-Za-z\u0600-\u06FF\u0900-\u097F]", text):
        return True                                   # only digits / symbols / emoji
    for tok in re.findall(r"[a-z]+", text.lower()):
        if len(tok) >= 5 and (re.search(r"[bcdfghjklmnpqrstvwxz]{6,}", tok)
                              or any(k in tok for k in ("qwert", "asdf", "zxcv", "hjkl", "uiop"))):
            return True
        if re.search(r"(.)\1{4,}", tok) or re.search(r"(\w{2,3})\1{2,}", tok):
            return True                               # "aaaaaa", "hahahaha"
    return False


def classify(raw: str) -> str:
    """Normalize Incoming Message: command detection is by WHOLE message ('I am done' is not DONE)."""
    text = raw.strip()
    if not text:
        return "EMPTY"
    if len(text) > MAX_MESSAGE_LEN:
        return "TOO_LONG"
    cleaned = re.sub(r"[\s.!?,;:]+$", "", text).upper()
    if cleaned in COMMANDS:
        return cleaned
    tokens = cleaned.split()
    if 1 < len(tokens) <= 3 and len(set(tokens)) == 1 and tokens[0] in COMMANDS:
        return tokens[0]                              # "START START" / "DONE DONE DONE"
    if GREETING_RE.match(cleaned):
        return "GREETING"
    if looks_like_garbage(text):
        return "GARBAGE"
    return "FREE_TEXT"


ROUTE_MAP = {"START": "PROGRESS", "CONTINUE": "PROGRESS", "GREETING": "PROGRESS", "DONE": "VERIFY", "SIGNED": "VERIFY",
             "STATUS": "STATUS", "HELP": "HELP", "SUPPORT": "HANDOFF", "SALES": "HANDOFF", "PAUSE": "PAUSE",
             "FREE_TEXT": "FAQ", "GARBAGE": "FALLBACK", "TOO_LONG": "FALLBACK"}


class OnboardingBot:
    """Session logic. Progress is NEVER stored here - it is re-read from the status API on every message."""

    def __init__(self, api: MockPlacidWayAPI, faq_fallback: Optional[Callable[[str, Optional[str]], Optional[str]]] = None):
        self.api = api
        # Optional extension point: an AI FAQ answerer (e.g. LangChain) restricted to APPROVED FAQ text.
        # Signature: (question, step_key) -> answer or None. Deterministic code below still owns all state/decisions.
        self.faq_fallback = faq_fallback
        self.reset()

    def reset(self) -> None:
        self.opted_out = False
        self.paused = False
        self.last_step_key: Optional[str] = None     # last step we sent the provider to (for "Step N complete" checks)
        self.unknown_streak = 0                      # consecutive messages we could not understand
        self.handoffs: list[dict] = []               # MOCK helpdesk queue
        self.history: list[tuple[str, str]] = []     # (role, text) - used as chat history for handoffs

    # ------------------------------------------------------------------ public
    def handle(self, text) -> BotTurn:
        trace = new_trace()
        raw = text if isinstance(text, str) else ""
        kind = "EMPTY"
        try:
            kind, out = self._process(raw, trace)
        except Exception as exc:  # noqa: BLE001 - the demo must never crash on odd input
            out = Outcome(reply="Sorry, something went wrong on our side. Reply **SUPPORT** and our team will help you.",
                          route="ERROR", detail=f"{type(exc).__name__}: {exc}", error_type="UNEXPECTED_ERROR")
        return self._finish(raw, kind, out, trace)

    # ------------------------------------------------------------------ pipeline
    def _process(self, raw: str, trace: dict) -> tuple[str, Outcome]:
        preview = (raw.strip()[:40] + "...") if len(raw.strip()) > 40 else raw.strip()
        mark(trace, "Incoming WhatsApp Webhook", "ok", f'From=whatsapp:{PROVIDER_PHONE}  Body="{preview}"')
        kind = classify(raw)
        mark(trace, "Normalize Incoming Message", "ok", f"classified as {kind}")

        # ---- Input Valid? / STOP? --------------------------------------------------------------
        if self.opted_out and kind != "START":
            mark(trace, "Input Valid? / STOP?", "error", "sender opted out earlier -> no automated reply")
            return kind, Outcome(note="Bot stays silent after STOP (opt-out). The user must send START to opt back in.",
                                 route="SUPPRESSED", detail="opted out", suppress=True, error_type="OPTED_OUT")
        if kind == "EMPTY":
            mark(trace, "Input Valid? / STOP?", "error", "empty message body (INVALID_MESSAGE)")
            return kind, Outcome(reply=f"Sorry, I didn't receive a readable message. Reply **HELP** for guidance, or one of: {COMMANDS_LINE}.",
                                 route="ERROR", detail="INVALID_MESSAGE", error_type="INVALID_MESSAGE")
        mark(trace, "Input Valid? / STOP?", "ok", "valid input" + ("; STOP command bypasses the API" if kind == "STOP" else ""))
        if kind == "STOP":
            return kind, Outcome(reply="You've been unsubscribed from automated PlacidWay WhatsApp messages. You won't receive further automated messages. To opt back in, reply **START**.",
                                 route="STOP", detail="opt-out recorded", state_update={"opted_out": True, "paused": True})

        # ---- Status API check (every other message re-checks the backend) ---------------------
        try:
            body = self.api.get_status()
            mark(trace, "Fetch Provider Status (Mock API)", "ok", "HTTP 200 - fresh status fetched")
        except APIError as exc:
            mark(trace, "Fetch Provider Status (Mock API)", "error", str(exc))
            return kind, self._api_failure(kind, "API_FAILURE", str(exc))
        try:
            self._validate(body)
            mark(trace, "Validate API Response", "ok", "schema and status values valid")
        except StatusError as exc:
            mark(trace, "Validate API Response", "error", str(exc))
            return kind, self._api_failure(kind, "UNEXPECTED_STATUS", str(exc))
        progress = self._resolve(body)
        mark(trace, "Find First Incomplete Step", "ok",
             f"{progress.completed}/5 sections complete; first incomplete = {progress.first['label'] if progress.first else 'none (all complete)'}")

        # ---- Route by Command -> Build Reply --------------------------------------------------
        route = ROUTE_MAP[kind]
        mark(trace, "Route by Command", "ok", f"{kind} -> {route}")
        handler = {"PROGRESS": self._on_progress, "VERIFY": self._on_verify, "STATUS": self._on_status,
                   "HELP": self._on_help, "HANDOFF": self._on_handoff, "PAUSE": self._on_pause,
                   "FAQ": self._on_faq, "FALLBACK": self._on_fallback}[route]
        out = handler(kind, raw, progress)
        out.route = out.route or route
        # unknown-input streak: only FAQ misses / garbage / too-long count
        if out.error_type in ("UNRECOGNIZED", "GARBAGE", "TOO_LONG"):
            self.unknown_streak += 1
        else:
            self.unknown_streak = 0
        return kind, out

    def _finish(self, raw: str, kind: str, out: Outcome, trace: dict) -> BotTurn:
        """Send (mock) + Log: the single exit path. This is also where state is written."""
        if trace["Build Reply"]["status"] == "skipped":
            mark(trace, "Build Reply", "error" if out.error_type in ("API_FAILURE", "UNEXPECTED_STATUS", "UNEXPECTED_ERROR", "INVALID_MESSAGE") else "ok",
                 out.detail or out.route)
        if out.suppress or not out.reply:
            mark(trace, "Simulate Twilio Send (MOCK)", "skipped", "nothing sent")
        else:
            mark(trace, "Simulate Twilio Send (MOCK)", "ok", f"queued (mock) -> whatsapp:{PROVIDER_PHONE}")
        upd = out.state_update
        if "opted_out" in upd:
            self.opted_out = upd["opted_out"]
        if "paused" in upd:
            self.paused = upd["paused"]
        if "last_step_key" in upd:
            self.last_step_key = upd["last_step_key"]
        if out.handoff:
            self.handoffs.append(out.handoff)
        self.history.append(("user", raw[:300]))
        if out.reply:
            self.history.append(("bot", out.reply[:300]))
        self.history = self.history[-40:]
        mark(trace, "Log Interaction & Session", "ok",
             f"intent={kind}; route={out.route or '-'}" + (f"; error={out.error_type}" if out.error_type else "")
             + (f"; handoff->{out.handoff['team']}" if out.handoff else ""))
        return BotTurn(reply=out.reply, note=out.note, kind=kind, route=out.route, trace=trace,
                       handoff=out.handoff, error_type=out.error_type)

    # ------------------------------------------------------------------ status helpers
    @staticmethod
    def _validate(body: dict) -> None:
        if not body or not body.get("provider") or not body.get("application", {}).get("steps"):
            raise StatusError("empty or incomplete API response")
        if body["provider"].get("account_status") not in VALID_ACCOUNT_STATUS:
            raise StatusError(f"unexpected account_status {body['provider'].get('account_status')!r}")
        for s in SECTION_STEPS:
            st = body["application"]["steps"].get(s.key)
            if not st or st.get("status") not in VALID_STEP_STATUS:
                raise StatusError(f"unexpected status {st.get('status') if st else None!r} for step {s.key}")

    @staticmethod
    def _resolve(body: dict) -> Progress:
        """Find First Incomplete Step: activation first, then sections 1..5 in order."""
        rows = [{"stage": 0, "key": "account_activation", "label": "Account Activation",
                 "status": "COMPLETED" if body["provider"]["account_status"] == "ACTIVE" else "NOT_STARTED",
                 "missing_fields": []}]
        for s in SECTION_STEPS:
            st = body["application"]["steps"][s.key]
            rows.append({"stage": s.stage, "key": s.key, "label": s.label, "status": st["status"],
                         "missing_fields": list(st.get("missing_fields") or [])})
        first = next((r for r in rows if r["status"] != "COMPLETED"), None)
        completed = sum(1 for r in rows if r["stage"] > 0 and r["status"] == "COMPLETED")
        return Progress(rows, first, completed, body["provider"].get("name", "there"),
                        body["application"].get("overall_status", ""))

    @staticmethod
    def _url(step_key: str) -> str:
        s = STEP_BY_KEY[step_key]
        return ACTIVATION_LINK_MOCK if s.path is None else PORTAL_BASE_URL + s.path

    def _step_block(self, first: dict, with_questions_line: bool = False) -> str:
        step = STEP_BY_KEY[first["key"]]
        lines = []
        if step.lead:
            lines.append(step.lead)
        lines.append(f"**{step.label}**" if step.stage == 0 else f"**Step {step.stage} of {TOTAL_SECTIONS} — {step.label}**")
        lines.append(step.intro)
        lines.append(self._url(step.key))
        if first["status"] == "IN_PROGRESS" and first["missing_fields"]:
            lines.append("Still missing: " + ", ".join(first["missing_fields"]))
        lines.append(step.finish_hint)
        if with_questions_line:
            lines.append("Questions? Just ask me here.")
        return "\n".join(lines)

    @staticmethod
    def _complete_message(progress: Progress) -> str:
        done = "\n".join(f"✓ {s.label}" for s in SECTION_STEPS)
        return (f"**Application complete ✓**\nYou've completed all {TOTAL_SECTIONS} steps:\n{done}\n\n"
                "All sections are verified as complete in PlacidWay's system. No onboarding steps are pending.")

    # ------------------------------------------------------------------ failure paths
    def _api_failure(self, kind: str, error_type: str, detail: str) -> Outcome:
        """API down / unusable data: never change progress. SUPPORT and SALES must still work."""
        if kind in ("SUPPORT", "SALES"):
            team = "SALES" if kind == "SALES" else "SUPPORT"
            return Outcome(reply=f"Thank you. I've notified our {team.lower()} team. They will contact you here on WhatsApp.",
                           route="HANDOFF", detail=f"{team} handoff created without status data ({error_type})",
                           handoff=self._handoff(team, f"USER_REQUESTED_{team}_WHILE_{error_type}", None),
                           error_type=error_type, note="The status API is unavailable, but human handoff still works.")
        if error_type == "UNEXPECTED_STATUS":
            return Outcome(reply="I found something unexpected in your application record, so I've asked our support team to review it. Your progress has not been changed.",
                           route="HANDOFF", detail="unexpected data -> support notified",
                           handoff=self._handoff("SUPPORT", "UNEXPECTED_STATUS_VALUE", None, detail), error_type=error_type,
                           note="Corrupt data detected by 'Validate API Response'. The bot refuses to guess and escalates.")
        return Outcome(reply="Sorry, I couldn't check your application status right now. Please try again in a few minutes, or reply **SUPPORT** to reach our team. Your progress has not been changed.",
                       route="ERROR", detail="status API unavailable", error_type=error_type,
                       note="The bot cannot verify anything without the API, so it does NOT guess or advance the provider.")

    def _handoff(self, team: str, reason: str, progress: Optional[Progress], error_detail: str = "") -> dict:
        history = self.history[-6:]
        return {"team": team, "reason": reason,
                "provider_id": MockPlacidWayAPI.PROVIDER["provider_id"] if progress else None,
                "provider_name": progress.provider_name if progress else None,
                "current_step": (progress.first["label"] if progress.first else "ALL_COMPLETE") if progress else "UNKNOWN (status unavailable)",
                "chat_history": [{"role": r, "text": t} for r, t in history],
                "created_at": datetime.now().strftime("%H:%M:%S"), "error_detail": error_detail or None}

    # ------------------------------------------------------------------ route handlers
    def _on_progress(self, kind: str, raw: str, p: Progress) -> Outcome:
        """START / CONTINUE / greeting: always the FIRST INCOMPLETE step - never restart from Step 1."""
        state = {"last_step_key": p.first["key"] if p.first else "ALL_COMPLETE"}
        if kind in ("START", "CONTINUE"):
            state["paused"] = False
        if kind == "START":
            state["opted_out"] = False
        if p.first is None:
            return Outcome(reply=self._complete_message(p), state_update=state, detail="all sections complete")
        if kind == "START" and p.completed == 0 and p.first["stage"] != 0:
            head = f"Welcome to PlacidWay.\nYou have {TOTAL_SECTIONS} steps to complete. I'll send them one at a time.\nLet's start."
            return Outcome(reply=head + "\n\n" + self._step_block(p.first, True), state_update=state, detail=f"sent {p.first['label']}")
        if p.first["stage"] == 0:
            head = "Welcome to PlacidWay.\nFirst, let's activate your account."
            return Outcome(reply=head + "\n\n" + self._step_block(p.first, kind == "START"), state_update=state, detail="sent activation step")
        head = (f"Welcome back, {p.provider_name}! You have completed {p.completed} of {TOTAL_SECTIONS} application sections. "
                "Let's continue where you left off.")
        return Outcome(reply=head + "\n\n" + self._step_block(p.first), state_update=state, detail=f"resumed at {p.first['label']}")

    def _on_verify(self, kind: str, raw: str, p: Progress) -> Outcome:
        """DONE / SIGNED: a *request to re-check*. Nothing is marked complete by the message itself."""
        head = "Thank you. Let me check your agreement status." if kind == "SIGNED" else "Great. Let me check your application."
        keys = [r["key"] for r in p.rows]
        prev = self.last_step_key
        state = {"last_step_key": p.first["key"] if p.first else "ALL_COMPLETE"}
        if p.first is None:
            note = ""
            if prev and prev != "ALL_COMPLETE":
                note = "Verified: the backend now shows every section complete."
            return Outcome(reply=f"{head}\n\n{self._complete_message(p)}", state_update=state, detail="verified: all complete", note=note)
        if prev in keys and keys.index(prev) < keys.index(p.first["key"]):
            newly = [r for r in p.rows[keys.index(prev):keys.index(p.first["key"])] if r["status"] == "COMPLETED"]
            lines = "\n".join(f"{r['label']} complete ✓" if r["stage"] == 0 else f"Step {r['stage']} complete ✓" for r in newly)
            body = f"{lines}\n**{p.completed} of {TOTAL_SECTIONS} completed**\n\nNext:\n{self._step_block(p.first)}"
            return Outcome(reply=f"{head}\n\n{body}", state_update=state, detail=f"verified {len(newly)} step(s) complete; next = {p.first['label']}",
                           note="This advanced only because the backend now reports the step as COMPLETED.")
        if prev == p.first["key"]:
            step = STEP_BY_KEY[p.first["key"]]
            name = step.label if step.stage == 0 else f"Step {step.stage}"
            miss = f"\nStill missing: {', '.join(p.first['missing_fields'])}" if p.first["missing_fields"] else ""
            body = (f"It looks like {name} is not fully completed yet.{miss}\nPlease check the remaining fields here:\n"
                    f"{self._url(step.key)}\nMessage me when you're finished.")
            return Outcome(reply=f"{head}\n\n{body}", state_update=state, detail="backend says step still incomplete",
                           note="The provider said DONE, but the status API says otherwise - so the bot does not advance. "
                                "Demo tip: click '✅ Complete current step in the portal' in the sidebar, then send DONE again.")
        return Outcome(reply=f"{head}\n\nHere's where you are:\n{self._step_block(p.first)}", state_update=state, detail="no earlier step on record; showed current step")

    def _on_status(self, kind: str, raw: str, p: Progress) -> Outcome:
        icon = {"COMPLETED": "✅", "IN_PROGRESS": "🟡", "NOT_STARTED": "⬜"}
        lines = []
        for r in p.rows:
            miss = f" (missing: {', '.join(r['missing_fields'])})" if r["missing_fields"] else ""
            lines.append(f"{icon[r['status']]} {r['label']} — {r['status']}{miss}")
        tail = ("All application sections are complete." if p.first is None
                else f"Your next step is **{p.first['label']}**:\n{self._url(p.first['key'])}")
        return Outcome(reply="📋 **Your verified onboarding progress** (from PlacidWay's records)\n\n" + "\n".join(lines)
                       + f"\n\nYou have completed {p.completed} of {TOTAL_SECTIONS} application sections.\n{tail}",
                       state_update={"last_step_key": p.first["key"] if p.first else "ALL_COMPLETE"}, detail="progress listed from API data")

    def _on_help(self, kind: str, raw: str, p: Progress) -> Outcome:
        if p.first is None:
            return Outcome(reply=f"All your onboarding sections are complete. Reply **STATUS** to review, or **SUPPORT** to reach our team.\n\nCommands: {COMMANDS_LINE}",
                           detail="help (all complete)")
        step = STEP_BY_KEY[p.first["key"]]
        return Outcome(reply=f"Help for **{step.label}**:\n{step.help_text}\n\nOpen it here:\n{self._url(step.key)}\n\nCommands: {COMMANDS_LINE}",
                       detail=f"help for {step.label}")

    def _on_handoff(self, kind: str, raw: str, p: Progress) -> Outcome:
        team = "SALES" if kind == "SALES" else "SUPPORT"
        ho = self._handoff(team, f"USER_REQUESTED_{team}", p)
        return Outcome(reply=f"Thank you, {p.provider_name}. I've notified our {team.lower()} team with your details and current progress. They will contact you here on WhatsApp.",
                       handoff=ho, detail=f"{team} ticket created (provider ID, current step, chat history)")

    def _on_pause(self, kind: str, raw: str, p: Progress) -> Outcome:
        return Outcome(reply="Okay. I've paused automated reminders for your application. Your progress is saved - you can message me any time. Reply **CONTINUE** to resume, or **STOP** to opt out of automated messages.",
                       state_update={"paused": True}, detail="reminders paused (progress untouched)")

    def _on_faq(self, kind: str, raw: str, p: Progress) -> Outcome:
        """Questions: blocked topics first (deterministic), then approved FAQ. Never invents requirements."""
        t = raw.lower()
        if LEGAL_RE.search(t):
            return Outcome(reply="I can't give legal advice. For questions about the agreement terms, reply **SUPPORT** and our team will help.",
                           detail="blocked topic: legal advice", note="Guardrail: legal advice is out of scope for the bot.")
        if PRICING_RE.search(t):
            return Outcome(reply="I can't discuss pricing or fees. Reply **SALES** and our team will walk you through the options.",
                           detail="blocked topic: pricing", note="Guardrail: pricing/package advice goes to the sales team.")
        step = STEP_BY_KEY[p.first["key"]] if p.first else None
        link = f"\n\nContinue here:\n{self._url(step.key)}" if step else ""
        if step:
            for pattern, answer in step.faq:
                if re.search(pattern, t):
                    return Outcome(reply=answer + link, detail=f"approved FAQ ({step.label})",
                                   note="Answered from approved FAQ content (demo text from the client's Scope of Work).")
        for pattern, answer in GENERAL_FAQ:
            if re.search(pattern, t):
                return Outcome(reply=answer + link, detail="approved FAQ (general)",
                               note="Answered from approved FAQ content (demo text from the client's Scope of Work).")
        if self.faq_fallback:                         # optional AI layer; guarded so it can never claim completion
            answer = self.faq_fallback(raw, step.key if step else None)
            if answer and not re.search(r"(has been completed|is complete|congratulations|approved)", answer, re.I):
                return Outcome(reply=answer + link, detail="AI FAQ answer (guarded)")
        return self._on_fallback(kind, raw, p, miss=True)

    def _on_fallback(self, kind: str, raw: str, p: Progress, miss: bool = False) -> Outcome:
        """Garbage / unknown text: never crash, never guess - guide back to the current step."""
        error_type = "UNRECOGNIZED" if miss or kind == "FREE_TEXT" else ("TOO_LONG" if kind == "TOO_LONG" else "GARBAGE")
        opener = {"UNRECOGNIZED": "I'm not sure how to help with that one.",
                  "TOO_LONG": "That message is a bit long for me - please keep it short.",
                  "GARBAGE": "Sorry, I didn't understand that."}[error_type]
        if p.first is None:
            where = "All your onboarding sections are complete. Reply **STATUS** to review."
        else:
            step = STEP_BY_KEY[p.first["key"]]
            label = step.label if step.stage == 0 else f"Step {step.stage} of {TOTAL_SECTIONS} — {step.label}"
            where = f"You're currently on **{label}**:\n{self._url(step.key)}\nWhen you've finished, reply **DONE**."
        streak = self.unknown_streak + 1
        extra = "\n\nYou can also reply **HELP** for guidance, **STATUS** for your progress, or **SUPPORT** to talk to our team."
        if streak >= 3:
            extra = "\n\nIt seems I'm not getting through - reply **SUPPORT** and I'll connect you with the PlacidWay team."
        return Outcome(reply=f"{opener}\n\n{where}{extra}", error_type=error_type, route="FALLBACK",
                       detail=f"unrecognized input (streak {streak})",
                       note="Fallback: no crash, no guessing - the bot guides the provider back to the current step."
                            + (" After 3 misses it offers human support." if streak < 3 else ""))


# =============================================================================
# 4. STREAMLIT UI
# =============================================================================

@dataclass
class Msg:
    role: str   # "user" | "bot" | "note"
    text: str
    ts: str


class DemoSession:
    """Everything that must survive Streamlit reruns (stored in st.session_state)."""

    def __init__(self, scenario: str = DEFAULT_SCENARIO):
        self.scenario = scenario
        self.api = MockPlacidWayAPI(scenario)
        self.bot = OnboardingBot(self.api)
        self.messages: list[Msg] = []
        self.last_turn: Optional[BotTurn] = None
        self.restart(scenario)

    def restart(self, scenario: Optional[str] = None) -> None:
        if scenario:
            self.scenario = scenario
        flags = (self.api.api_down, self.api.corrupt)
        self.api.load_scenario(self.scenario)
        self.api.api_down, self.api.corrupt = flags
        self.bot.reset()
        self.last_turn = None
        now = _now()
        self.messages = [
            Msg("note", "Demo started. Business-initiated first message below (a WhatsApp template - allowed outside the 24h window).", now),
            Msg("bot", "Hello! Welcome to PlacidWay 👋\nYour provider account is ready. Reply **START** to begin your onboarding.\nIf you need assistance, reply **HELP**.", now),
        ]

    def send(self, text: str) -> None:
        """One inbound WhatsApp message -> run the pipeline -> append bot reply (+ demo note)."""
        self.messages.append(Msg("user", text if text.strip() else "(empty message)", _now()))
        turn = self.bot.handle(text)
        self.last_turn = turn
        if turn.reply:
            self.messages.append(Msg("bot", turn.reply, _now()))
        if turn.note:
            self.messages.append(Msg("note", "Demo note: " + turn.note, _now()))

    def portal_complete(self) -> None:
        label = self.api.complete_current_step()
        self.messages.append(Msg("note", f"Portal (simulated): provider finished '{label}'. No WhatsApp message was sent - the bot only learns this when it re-checks the API."
                                 if label else "Portal (simulated): everything is already complete.", _now()))

    def portal_partial(self) -> None:
        label = self.api.start_current_step()
        self.messages.append(Msg("note", f"Portal (simulated): provider saved part of '{label}' and left (IN_PROGRESS, fields missing)."
                                 if label else "Portal (simulated): nothing to start (account not activated, or all complete).", _now()))


EDGE_MESSAGES = {
    "Gibberish: asdfghjkl": "asdfghjkl",
    "Symbols only: ?!?!?!": "?!?!?!",
    "Emoji only": "😂😂👍",
    "Random words (ulta seedha)": "ulta seedha kuch bhi",
    "Not a command: 'I am done'": "I am done with this step",
    "Repeated command: START START": "START START",
    "Lowercase + punctuation: done!!": "done!!",
    "Empty message (spaces only)": "   ",
    "Very long message (1500+ chars)": "blah " * 300,
    "HTML injection: <script>": "<script>alert(1)</script>",
    "Legal question": "Is this agreement legally binding? Do I need a lawyer?",
    "Pricing question": "How much does it cost?",
    "Which package should I choose?": "Which package should I choose?",
    "FAQ: what should I upload?": "What should I upload?",
    "FAQ: can I do this later?": "Can I do this later?",
}

_URL_RE = re.compile(r"(https?://[^\s<]+)")


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def fmt(text: str) -> str:
    """Message text -> safe HTML. Escape FIRST (users can type anything), then add bold/links/line breaks."""
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = _URL_RE.sub(r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', s)
    return s.replace("\n", "<br>")


def chat_html(messages: list[Msg]) -> str:
    # NOTE: built without newlines/indentation so Markdown does not treat it as a code block.
    rows = []
    for m in messages:
        if m.role == "note":
            rows.append(f'<div class="wa-note">{fmt(m.text)}</div>')
        else:
            tick = ' <span class="wa-tick">✓✓</span>' if m.role == "user" else ""
            rows.append(f'<div class="wa-row {m.role}"><div class="wa-bubble {m.role}">{fmt(m.text)}'
                        f'<div class="wa-time">{m.ts}{tick}</div></div></div>')
    header = ('<div class="wa-header"><div class="wa-avatar">P</div><div><div class="wa-title">PlacidWay Onboarding</div>'
              f'<div class="wa-sub">Provider: {html.escape(MockPlacidWayAPI.PROVIDER["name"])} · +1 555 010 0003 · online</div></div></div>')
    return f'<div class="wa-phone">{header}<div class="wa-chat">{"".join(rows)}</div></div>'


def trace_html(trace: Optional[dict]) -> str:
    chips = []
    for i, node in enumerate(NODES, start=1):
        info = (trace or {}).get(node, {"status": "idle", "detail": ""})
        status = info["status"] if trace else "idle"
        icon = {"ok": "✅", "error": "❌", "skipped": "⏭️", "idle": "⚪"}[status]
        detail = f'<div class="tr-detail">{html.escape(info["detail"])}</div>' if info["detail"] else ""
        chips.append(f'<div class="tr-node {status}">{icon} <b>{i}. {html.escape(node)}</b>{detail}</div>')
    return '<div class="tr-wrap">' + '<div class="tr-arrow">↓</div>'.join(chips) + "</div>"


CSS = """
<style>
.block-container {max-width: 1280px; padding-top: 1.2rem;}
.wa-phone {border-radius: 14px; overflow: hidden; border: 1px solid #cfcfcf; box-shadow: 0 2px 14px rgba(0,0,0,.15); max-width: 520px; margin: 0 auto;}
.wa-header {background:#075E54; color:#fff; padding:10px 14px; display:flex; align-items:center; gap:10px;}
.wa-avatar {width:38px; height:38px; border-radius:50%; background:#25D366; display:flex; align-items:center; justify-content:center; font-weight:700; color:#fff;}
.wa-title {font-weight:600; line-height:1.15;} .wa-sub {font-size:12px; opacity:.85;}
.wa-chat {background:#ECE5DD; padding:12px 10px 16px; min-height:430px; display:flex; flex-direction:column; gap:6px;}
.wa-row {display:flex;} .wa-row.user {justify-content:flex-end;}
.wa-bubble {max-width:80%; padding:7px 10px 4px; border-radius:8px; font-size:14.5px; line-height:1.38; color:#111; box-shadow:0 1px 1px rgba(0,0,0,.14); word-break:break-word;}
.wa-bubble.user {background:#DCF8C6; border-top-right-radius:0;} .wa-bubble.bot {background:#FFFFFF; border-top-left-radius:0;}
.wa-bubble a {color:#0a63b0;} .wa-time {font-size:11px; color:#667781; text-align:right; margin-top:2px;} .wa-tick {color:#53bdeb;}
.wa-note {align-self:center; background:#FFF3C4; color:#54410A; font-size:12px; padding:5px 10px; border-radius:8px; max-width:92%; text-align:center;}
.tr-wrap {font-size:13px;} .tr-node {border-radius:8px; padding:6px 10px; border:1px solid #ddd; color:#111;}
.tr-node.ok {background:#E7F6EA; border-color:#9fd6ab;} .tr-node.error {background:#FDE7E7; border-color:#e5a3a3;}
.tr-node.skipped {background:#F1F1F1; color:#666;} .tr-node.idle {background:#FAFAFA; color:#888;}
.tr-detail {font-size:12px; opacity:.85; margin-top:2px;} .tr-arrow {text-align:center; color:#888; line-height:1;}
</style>
"""

ICON = {"COMPLETED": "✅ COMPLETED", "IN_PROGRESS": "🟡 IN_PROGRESS", "NOT_STARTED": "⬜ NOT_STARTED"}


def main() -> None:
    import streamlit as st   # imported here so the engine above can be tested without Streamlit installed

    st.set_page_config(page_title="PlacidWay WhatsApp Onboarding Demo", page_icon="💬", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    if "demo" not in st.session_state:
        st.session_state["demo"] = DemoSession()
    demo: DemoSession = st.session_state["demo"]

    # ------------------------------------------------------------------ sidebar: demo controls
    def on_scenario_change() -> None:
        demo.restart(st.session_state["scenario_select"])

    def send_edge_message() -> None:
        demo.send(EDGE_MESSAGES[st.session_state["edge_select"]])

    with st.sidebar:
        st.header("🎛️ Demo controls")
        st.caption("These controls act like the *outside world*: the provider using the portal, or things going wrong. They are not part of the chat.")
        st.selectbox("Provider scenario", list(SCENARIOS), index=list(SCENARIOS).index(demo.scenario),
                     key="scenario_select", on_change=on_scenario_change)
        st.button("🔄 Restart demo", key="restart", on_click=demo.restart)

        st.subheader("Simulate the portal")
        st.button("✅ Complete current step in the portal", key="portal_done", on_click=demo.portal_complete)
        st.button("🟡 Save part of the step, then leave", key="portal_partial", on_click=demo.portal_partial)

        st.subheader("Simulate problems")
        demo.api.api_down = st.checkbox("🔌 Status API is DOWN (503)", value=demo.api.api_down, key="api_down")
        demo.api.corrupt = st.checkbox("🧪 API returns corrupt data", value=demo.api.corrupt, key="api_corrupt")
        st.selectbox("Edge-case message", list(EDGE_MESSAGES), key="edge_select")
        st.button("📨 Send edge-case message", key="send_edge", on_click=send_edge_message)

        st.subheader("Quick commands")
        cols = st.columns(3)
        for i, cmd in enumerate(("START", "CONTINUE", "STATUS", "HELP", "DONE", "SUPPORT", "SALES", "PAUSE", "STOP")):
            cols[i % 3].button(cmd, key=f"cmd_{cmd}", on_click=demo.send, args=(cmd,))
        st.caption("Also try: SIGNED (agreement step). START and DONE/SIGNED are proposed commands pending client confirmation.")

    # ------------------------------------------------------------------ chat input (sticky at the bottom)
    prompt = st.chat_input("Type a WhatsApp message… (try START)")
    if prompt is not None:
        demo.send(prompt)

    # ------------------------------------------------------------------ main layout
    st.title("💬 PlacidWay Provider Onboarding — WhatsApp demo")
    st.caption("Option A: *Guide and Verify.* Providers complete forms on the PlacidWay portal; the bot guides them and verifies progress "
               "with the status API. **Everything here is simulated.**")
    left, right = st.columns([1, 1.05])

    with left:
        st.markdown(chat_html(demo.messages), unsafe_allow_html=True)

    with right:
        tab_backend, tab_flow, tab_session = st.tabs(["🔍 Backend verification", "⚙️ n8n workflow trace", "📥 Session & handoff"])

        with tab_backend:
            snap = demo.api.snapshot()
            first = demo.api._first_incomplete()
            c1, c2, c3 = st.columns(3)
            c1.metric("Mock API", "🔴 DOWN" if demo.api.api_down else "🟢 UP")
            c2.metric("Account", snap["provider"]["account_status"])
            c3.metric("Sections complete", f"{sum(1 for v in snap['application']['steps'].values() if v['status'] == 'COMPLETED')}/5")
            if demo.api.api_down:
                st.error("API is DOWN: the bot cannot verify anything, so it will not advance or guess. SUPPORT/SALES still work.")
            elif demo.api.corrupt:
                st.warning("API will return an unexpected status value: 'Validate API Response' should catch it and escalate.")
            st.info(f"**First incomplete step (per backend):** {first.label if first else 'none - application complete'}")
            rows = [{"Stage": 0, "Section": "Account Activation",
                     "Status (source of truth)": ICON["COMPLETED" if snap["provider"]["account_status"] == "ACTIVE" else "NOT_STARTED"],
                     "Missing fields": ""}]
            for s in SECTION_STEPS:
                st_ = snap["application"]["steps"][s.key]
                rows.append({"Stage": s.stage, "Section": s.label, "Status (source of truth)": ICON[st_["status"]],
                             "Missing fields": ", ".join(st_["missing_fields"])})
            st.table(rows)
            st.caption("🔑 The provider typing DONE changes nothing here. Only the portal (sidebar button) changes this table, "
                       "and the bot sees it only by calling the API again.")
            with st.expander("Raw mock API response (proposed contract - NOT approved by PlacidWay)"):
                st.json(snap)
            with st.expander(f"API call log ({len(demo.api.calls)} calls)"):
                st.table(demo.api.calls[-8:][::-1] if demo.api.calls else [{"time": "-", "request": "no calls yet", "result": "-"}])

        with tab_flow:
            st.markdown("**Execution of the last message** through the same nodes as the n8n *Main Chatbot* workflow:")
            st.markdown(trace_html(demo.last_turn.trace if demo.last_turn else None), unsafe_allow_html=True)
            st.caption("✅ ran · ❌ error/blocked branch · ⏭️ not reached. The Status API node runs for every message except STOP and empty input.")

        with tab_session:
            bot = demo.bot
            a, b, c = st.columns(3)
            a.metric("Opted out (STOP)", "yes" if bot.opted_out else "no")
            b.metric("Reminders paused", "yes" if bot.paused else "no")
            c.metric("Last step sent", bot.last_step_key or "-")
            st.caption(f"Consecutive messages not understood: {bot.unknown_streak} (offers SUPPORT at 3)")
            st.markdown("**Human handoff queue (mock helpdesk)**")
            if bot.handoffs:
                for h in reversed(bot.handoffs):
                    with st.expander(f"{h['created_at']} → {h['team']} · {h['reason']}"):
                        st.json(h)
            else:
                st.write("No handoffs yet - try SUPPORT or SALES.")


if __name__ == "__main__":
    main()
