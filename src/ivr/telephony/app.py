"""FastAPI webhook service wrapping CoordinatorFlow for real phone calls via
Twilio. Uses Twilio's built-in <Gather input="speech"> for ASR and <Say> for
TTS in this first pass -- fastest path to an actual working call. Swapping
in dedicated ASR/TTS providers later only touches this file; CoordinatorFlow
and AuthFlow don't know or care how speech got turned into text.

--- Twilio console setup ---
1. Sign up at twilio.com, verify a phone number, get a free trial number
   (Twilio Console -> Phone Numbers -> Buy a number).
2. Run this app: uvicorn ivr.telephony.app:app --reload --port 8000
3. Make it publicly reachable (a Codespaces forwarded port set to Public,
   or an ngrok tunnel) and note the https URL.
4. In the Twilio Console, open your number -> Voice Configuration ->
   "A call comes in" -> Webhook -> {your public URL}/voice/incoming -> HTTP POST.
5. Call your Twilio number.

Signature validation (TWILIO_VALIDATE_SIGNATURES) is OFF by default -- see
config.py for why, and turn it on with PUBLIC_BASE_URL set correctly before
any real deployment.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()  # must run before any ivr.* import below -- config modules read os.environ at import time

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402
from twilio.twiml.voice_response import Gather, VoiceResponse  # noqa: E402

from ivr.auth.cbs_dummy import DummyCBSClient  # noqa: E402
from ivr.auth.otp_dummy import DummyOTPGateway  # noqa: E402
from ivr.auth.tier_config import IntentTierMap  # noqa: E402
from ivr.coordinator.build_intent_classifier import build_intent_classifier  # noqa: E402
from ivr.coordinator.flow import CoordinatorFlow, CoordinatorState, CoordinatorStatus  # noqa: E402
from ivr.telephony import config  # noqa: E402
from ivr.telephony.digit_normalizer import normalize_spoken_digits  # noqa: E402
from ivr.telephony.session_store import CallSession, SessionStore  # noqa: E402

logging.basicConfig(level=logging.INFO)  # otherwise the fake-OTP dev logging below is silently swallowed
logger = logging.getLogger("ivr.telephony")

app = FastAPI(title="ABC Retail Bank IVR -- Twilio webhook service")

# Shared, module-level backends -- CBS/OTP are the same dummy data the CLI
# harness and test suite use; intent classification is real Groq-based LLM
# classification if GROQ_API_KEY is set (build_intent_classifier.py), else
# keyword matching. A real deployment would inject real CBS/OTP clients
# here instead; nothing else in this file would need to change.
_cbs = DummyCBSClient()
_otp_gateway = DummyOTPGateway()
_tier_map = IntentTierMap.load()
_intent_classifier = build_intent_classifier(_tier_map)
_sessions = SessionStore()

_FACTOR_HINTS = "zero,one,two,three,four,five,six,seven,eight,nine,resend"


async def _verify_twilio_signature(request: Request, form: dict) -> None:
    if not config.TWILIO_VALIDATE_SIGNATURES:
        return
    if not config.TWILIO_AUTH_TOKEN or not config.PUBLIC_BASE_URL:
        raise HTTPException(
            500, "TWILIO_VALIDATE_SIGNATURES is on but TWILIO_AUTH_TOKEN/PUBLIC_BASE_URL are not set"
        )
    signature = request.headers.get("X-Twilio-Signature", "")
    url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    if not validator.validate(url, form, signature):
        raise HTTPException(403, "Invalid Twilio signature")


def _xml(vr: VoiceResponse) -> Response:
    return Response(content=str(vr), media_type="application/xml")


def _gather_action_url() -> str:
    # Prefer an absolute URL built from PUBLIC_BASE_URL -- relying on Twilio
    # to resolve a relative "/voice/gather" against whatever host it thinks
    # it called has proven unreliable behind at least Codespaces' forwarded
    # domains (manifests as Twilio's own "we cannot reach your server"
    # message right after the first request succeeded fine). Falls back to
    # relative only if PUBLIC_BASE_URL isn't set.
    if config.PUBLIC_BASE_URL:
        return config.PUBLIC_BASE_URL.rstrip("/") + "/voice/gather"
    return "/voice/gather"


def _gather_response(prompt: str, *, hints: str | None = None) -> Response:
    vr = VoiceResponse()
    gather = Gather(
        input="speech", action=_gather_action_url(), method="POST",
        speech_timeout="auto", language="en-IN", hints=hints,
    )
    gather.say(prompt)
    vr.append(gather)
    # If the caller says nothing at all before the gather times out, Twilio
    # still POSTs to `action` with no SpeechResult -- _dispatch treats that
    # as a confidence=0 turn, which every submit_* method already handles
    # via the same low-confidence reprompt path used for real low-confidence
    # speech. No separate no-input handling needed here.
    return _xml(vr)


def _hangup_response(prompt: str | None, *, transfer_to_csr: bool = False) -> Response:
    vr = VoiceResponse()
    if prompt:
        vr.say(prompt)
    if transfer_to_csr and config.CSR_TRANSFER_NUMBER:
        vr.dial(config.CSR_TRANSFER_NUMBER)
    else:
        vr.hangup()
    return _xml(vr)


def _render(session: CallSession, state: CoordinatorState) -> Response:
    """Turns a CoordinatorState into the next TwiML response, advancing
    session.pending_status to match."""
    if state.status == CoordinatorStatus.READY_FOR_HANDOFF:
        # Placeholder script -- no real domain agent exists yet to actually
        # serve the intent. Logged for developer visibility only; a real
        # caller should never hear about implementation status.
        logger.info(
            "call %s: handed off to %s for '%s' (tier %s) -- no real domain agent, simulating completion",
            session.call_sid, state.agent_id, state.intent_id, state.tier_achieved,
        )
        next_state = session.coordinator.mark_task_completed()
        session.pending_status = next_state.status
        return _gather_response(next_state.prompt or "Is there anything else I can help you with?")

    if state.status == CoordinatorStatus.ESCALATED:
        session.pending_status = state.status
        _sessions.end(session.call_sid)
        return _hangup_response(state.prompt, transfer_to_csr=True)

    if state.status == CoordinatorStatus.CALL_ENDED:
        session.pending_status = state.status
        _sessions.end(session.call_sid)
        return _hangup_response(state.prompt)

    session.pending_status = state.status
    needs_digits = state.status in (
        CoordinatorStatus.NEEDS_MPIN, CoordinatorStatus.NEEDS_OTP, CoordinatorStatus.NEEDS_IDENTIFICATION,
    )
    if state.status == CoordinatorStatus.NEEDS_OTP and state.cif:
        # There's no real SMS gateway -- without this, the OTP path is
        # completely untestable on a real call (nowhere to read the code
        # from). Same dev-only visibility the CLI harness prints to console.
        mobile = _cbs.get_registered_mobile(state.cif)
        if mobile:
            try:
                logger.info("call %s: [DEV] fake SMS to %s: code is %s",
                            session.call_sid, mobile, _otp_gateway.last_code_sent_to(mobile))
            except LookupError:
                pass
    return _gather_response(state.prompt or "", hints=_FACTOR_HINTS if needs_digits else None)


@app.post("/voice/incoming")
async def voice_incoming(request: Request) -> Response:
    form = dict(await request.form())
    await _verify_twilio_signature(request, form)

    call_sid = form.get("CallSid", "")
    if not call_sid:
        raise HTTPException(400, "Missing CallSid")
    ani = form.get("From") or None

    coordinator = CoordinatorFlow(
        cbs=_cbs, otp_gateway=_otp_gateway, tier_map=_tier_map, intent_classifier=_intent_classifier
    )
    session = _sessions.create(call_sid, coordinator)
    state = coordinator.start(ani=ani)
    session.pending_status = state.status
    return _gather_response(state.prompt or "")


@app.post("/voice/gather")
async def voice_gather(request: Request) -> Response:
    form = dict(await request.form())
    await _verify_twilio_signature(request, form)

    call_sid = form.get("CallSid", "")
    session = _sessions.get(call_sid)
    if session is None:
        return _hangup_response("Sorry, something went wrong with this call. Please call back.")

    text = form.get("SpeechResult", "") or ""
    try:
        confidence = float(form.get("Confidence") or 0)
    except ValueError:
        confidence = 0.0

    coordinator = session.coordinator
    pending = session.pending_status

    if pending == CoordinatorStatus.NEEDS_INTENT:
        return _render(session, coordinator.submit_utterance(text, confidence=confidence))

    if pending == CoordinatorStatus.NEEDS_MPIN:
        return _render(session, coordinator.submit_mpin(normalize_spoken_digits(text), confidence=confidence))

    if pending == CoordinatorStatus.NEEDS_OTP:
        if text.strip().lower() == "resend":
            return _render(session, coordinator.request_otp_resend())
        return _render(session, coordinator.submit_otp(normalize_spoken_digits(text), confidence=confidence))

    if pending == CoordinatorStatus.NEEDS_IDENTIFICATION:
        digits = normalize_spoken_digits(text)
        if session.identification_step in (None, "account"):
            session.identification_step = "card"
            session.pending_account_last6 = digits
            return _gather_response("And the last 4 digits of your card?", hints=_FACTOR_HINTS)
        account_last6 = session.pending_account_last6 or ""
        session.identification_step = None
        session.pending_account_last6 = None
        return _render(session, coordinator.submit_identification(account_last6, digits, confidence=confidence))

    # READY_FOR_HANDOFF/ESCALATED/CALL_ENDED are always advanced past
    # immediately by _render() -- session.pending_status should never sit on
    # one of them between requests. Defensive fallback only.
    logger.error("call %s: /voice/gather hit with unexpected pending_status=%s", call_sid, pending)
    _sessions.end(call_sid)
    return _hangup_response("Sorry, something went wrong with this call. Please call back.")


@app.post("/voice/status")
async def voice_status(request: Request) -> Response:
    """Optional Twilio call status callback -- configure separately in the
    console (there's usually a distinct "call status changes" webhook field
    from the main "a call comes in" one). Logs every field Twilio sends
    (CallStatus, CallDuration, and on failure often SipResponseCode/
    ErrorCode) -- useful diagnostic visibility that doesn't require paid
    access to Twilio's own call log detail. Also ends the session when the
    call reaches a terminal status, rather than only when this app itself
    reaches one."""
    form = dict(await request.form())
    logger.info("call %s: status callback %s", form.get("CallSid", "?"), form)
    if form.get("CallStatus") in {"completed", "failed", "busy", "no-answer", "canceled"}:
        _sessions.end(form.get("CallSid", ""))
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
