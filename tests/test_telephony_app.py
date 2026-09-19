"""Simulates Twilio's actual webhook payload shape via FastAPI's TestClient
-- no live Twilio account or real phone call needed. Twilio always POSTs
application/x-www-form-urlencoded with these exact field names (CallSid,
From, SpeechResult, Confidence); TestClient's `data=` kwarg reproduces that
faithfully, so a passing test here means the real webhook contract works,
modulo actual telephony/ASR/TTS behavior itself.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from ivr.auth.cbs_dummy import DummyCBSClient
from ivr.auth.otp_dummy import DummyOTPGateway
from ivr.banking.banking_dummy import DummyBankingClient
from ivr.telephony import app as app_module
from ivr.telephony.session_store import SessionStore

ASHA_MOBILE = "9876500001"  # mpin 4321
VIKRAM_MOBILE = "9876500002"  # mpin 1111
MEERA_MOBILE = "9876500003"  # watchlist-flagged account


@pytest.fixture(autouse=True)
def isolated_app_state(monkeypatch):
    """app.py holds module-level singleton CBS/OTP/banking/session state,
    same as a real running service would -- reset it before every test so
    MPIN-fail counts, OTP codes, service-request tickets, and sessions from
    one test never leak into another. AgentRegistry isn't module-level (it's
    constructed fresh per call, inside voice_incoming(), since it owns a
    per-call ServiceRequestLimiter) so there's nothing to patch for it here
    -- each test's own /voice/incoming call builds one against the patched
    _banking automatically."""
    monkeypatch.setattr(app_module, "_cbs", DummyCBSClient())
    monkeypatch.setattr(app_module, "_otp_gateway", DummyOTPGateway())
    monkeypatch.setattr(app_module, "_banking", DummyBankingClient())
    monkeypatch.setattr(app_module, "_sessions", SessionStore())


@pytest.fixture
def client():
    return TestClient(app_module.app)


def _say_text(xml_text: str) -> str:
    say = ET.fromstring(xml_text).find(".//Say")
    return say.text if say is not None else ""


def _has_gather(xml_text: str) -> bool:
    return ET.fromstring(xml_text).find(".//Gather") is not None


def _has_hangup(xml_text: str) -> bool:
    return ET.fromstring(xml_text).find(".//Hangup") is not None


def _has_dial(xml_text: str) -> bool:
    return ET.fromstring(xml_text).find(".//Dial") is not None


def _gather_action(xml_text: str) -> str:
    return ET.fromstring(xml_text).find(".//Gather").get("action")


def _gather_attrs(xml_text: str) -> dict:
    return dict(ET.fromstring(xml_text).find(".//Gather").attrib)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_incoming_call_returns_greeting_and_gather(client):
    res = client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    assert res.status_code == 200
    assert "ABC Retail Bank" in _say_text(res.text)
    assert _has_gather(res.text)


def test_incoming_call_without_caller_id(client):
    res = client.post("/voice/incoming", data={"CallSid": "CA1"})
    assert res.status_code == 200
    assert _has_gather(res.text)


def test_gather_action_is_relative_when_public_base_url_unset(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "PUBLIC_BASE_URL", "")
    res = client.post("/voice/incoming", data={"CallSid": "CA1"})
    assert _gather_action(res.text) == "/voice/gather"


def test_gather_action_is_absolute_when_public_base_url_set(client, monkeypatch):
    # Relying on Twilio to resolve a relative action URL against whatever
    # host it thinks it called has proven unreliable behind at least
    # Codespaces' forwarded domains -- this is the fix for that.
    monkeypatch.setattr(app_module.config, "PUBLIC_BASE_URL", "https://example.ngrok.io")
    res = client.post("/voice/incoming", data={"CallSid": "CA1"})
    assert _gather_action(res.text) == "https://example.ngrok.io/voice/gather"


def test_intent_capture_uses_auto_speech_timeout(client):
    # Natural language doesn't pause mid-utterance the way digit recitation
    # does -- "auto" (Twilio's own end-of-speech detection) is fine here.
    res = client.post("/voice/incoming", data={"CallSid": "CA1"})
    attrs = _gather_attrs(res.text)
    assert attrs["speechTimeout"] == "auto"
    assert attrs["timeout"] == str(app_module.config.GATHER_TIMEOUT_SECONDS)


def test_digit_capture_uses_longer_fixed_speech_timeout(client):
    # A caller reciting an MPIN/OTP/account number from memory often pauses
    # mid-recitation -- "auto" can cut that off early. Applies to any prompt
    # that also carries digit hints (MPIN/OTP/identification).
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    attrs = _gather_attrs(res.text)
    assert attrs["speechTimeout"] == app_module.config.GATHER_SPEECH_TIMEOUT_DIGITS
    assert attrs["hints"]


def test_missing_call_sid_on_incoming_is_rejected(client):
    res = client.post("/voice/incoming", data={"From": ASHA_MOBILE})
    assert res.status_code == 400


def test_full_tier1_cli_matched_happy_path(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    assert "MPIN" in _say_text(res.text)

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
    # The Accounts Agent is real now -- this should be an actual balance, not
    # a placeholder skipping straight to "anything else?".
    assert "45,231.50" in _say_text(res.text)
    assert "anything else" in _say_text(res.text).lower()
    assert _has_gather(res.text)


def test_full_cli_mismatch_identify_then_otp_path(client):
    client.post("/voice/incoming", data={"CallSid": "CA2"})
    client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": "what's my balance", "Confidence": "0.9"})

    res = client.post(
        "/voice/gather", data={"CallSid": "CA2", "SpeechResult": "seven eight nine zero one two", "Confidence": "0.9"}
    )
    assert "card" in _say_text(res.text).lower()

    res = client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": "one one one one", "Confidence": "0.9"})
    assert "code" in _say_text(res.text).lower()

    code = app_module._otp_gateway.last_code_sent_to(ASHA_MOBILE)
    res = client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": code, "Confidence": "0.9"})
    assert "anything else" in _say_text(res.text).lower()


def test_otp_resend_via_spoken_command(client):
    client.post("/voice/incoming", data={"CallSid": "CA2"})
    client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    client.post(
        "/voice/gather", data={"CallSid": "CA2", "SpeechResult": "seven eight nine zero one two", "Confidence": "0.9"}
    )
    client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": "one one one one", "Confidence": "0.9"})

    res = client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": "resend"})
    assert "new code" in _say_text(res.text).lower()

    new_code = app_module._otp_gateway.last_code_sent_to(ASHA_MOBILE)
    res = client.post("/voice/gather", data={"CallSid": "CA2", "SpeechResult": new_code, "Confidence": "0.9"})
    assert "anything else" in _say_text(res.text).lower()


def test_saying_no_thanks_ends_call(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "no thanks", "Confidence": "0.9"})
    assert _has_hangup(res.text)
    assert "goodbye" in _say_text(res.text).lower()


def test_escalation_hangs_up_when_no_csr_number_configured(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "CSR_TRANSFER_NUMBER", "")
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": MEERA_MOBILE})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    assert _has_hangup(res.text)
    assert not _has_dial(res.text)
    assert "watchlist" not in res.text.lower()  # anti-enumeration holds at the telephony layer too


def test_escalation_dials_csr_when_number_configured(client, monkeypatch):
    monkeypatch.setattr(app_module.config, "CSR_TRANSFER_NUMBER", "+911234567890")
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": MEERA_MOBILE})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    assert _has_dial(res.text)
    assert "+911234567890" in res.text


def test_gather_with_unknown_call_sid_prompts_to_call_back(client):
    res = client.post("/voice/gather", data={"CallSid": "no-such-call", "SpeechResult": "hello"})
    assert _has_hangup(res.text)
    assert "call back" in _say_text(res.text).lower()


def test_no_speech_detected_is_treated_as_low_confidence_reprompt(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    # Twilio omits SpeechResult (and Confidence) entirely on a pure timeout.
    res = client.post("/voice/gather", data={"CallSid": "CA1"})
    assert _has_gather(res.text)
    assert "didn't catch" in _say_text(res.text).lower()


def test_word_form_digits_normalized_end_to_end(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": VIKRAM_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "one one one one", "Confidence": "0.9"})
    assert "anything else" in _say_text(res.text).lower()


def test_status_callback_ends_session(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    assert app_module._sessions.get("CA1") is not None

    res = client.post("/voice/status", data={"CallSid": "CA1", "CallStatus": "completed"})
    assert res.status_code == 204
    assert app_module._sessions.get("CA1") is None


# -- Domain agents (real, dummy-data-backed -- not the old fake-instant-completion placeholder) --


def test_change_of_address_slot_filling_confirms_then_records_a_ticket(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I need to update my address", "Confidence": "0.9"})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
    assert "new address" in _say_text(res.text).lower()

    res = client.post(
        "/voice/gather", data={"CallSid": "CA1", "SpeechResult": "221B Baker Street, Mumbai", "Confidence": "0.9"}
    )
    assert "shall i update this as your new address" in _say_text(res.text).lower()
    assert app_module._banking.list_service_requests("CIF1001") == []  # not committed until confirmed

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})
    assert "updated your address" in _say_text(res.text).lower()
    assert "anything else" in _say_text(res.text).lower()
    ticket = app_module._banking.list_service_requests("CIF1001")[0]
    assert ticket.details["new_address"] == "221B Baker Street, Mumbai"


def test_change_of_address_confirmation_declined_lets_caller_restate(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I need to update my address", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "221B Baker Street, Mumbai", "Confidence": "0.9"})

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "no", "Confidence": "0.9"})
    assert "correct address" in _say_text(res.text).lower()

    res = client.post(
        "/voice/gather", data={"CallSid": "CA1", "SpeechResult": "42 Park Avenue, Delhi", "Confidence": "0.9"}
    )
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})
    ticket = app_module._banking.list_service_requests("CIF1001")[0]
    assert ticket.details["new_address"] == "42 Park Avenue, Delhi"


def test_topic_switch_during_address_slot_filling_reroutes(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I need to update my address", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})

    res = client.post(
        "/voice/gather", data={"CallSid": "CA1", "SpeechResult": "actually what's my balance", "Confidence": "0.9"}
    )
    # Re-routed to the Accounts Agent instead of saving that sentence as an address.
    assert "45,231.50" in _say_text(res.text)
    assert app_module._banking.list_service_requests("CIF1001") == []


def test_cheque_book_request_confirms_before_committing(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I'd like a new chequebook", "Confidence": "0.9"})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
    assert "shall i go ahead" in _say_text(res.text).lower()
    assert app_module._banking.list_service_requests("CIF1001") == []  # not committed until confirmed

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})
    assert "cheque book request is confirmed" in _say_text(res.text).lower()
    assert app_module._banking.list_service_requests("CIF1001")[0].kind == "cheque_book_request"


def test_cheque_book_request_declined_is_not_submitted(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I'd like a new chequebook", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "no", "Confidence": "0.9"})
    assert "haven't submitted" in _say_text(res.text).lower()
    assert app_module._banking.list_service_requests("CIF1001") == []


def test_service_request_rate_limit_escalates(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    # Authenticate once (Tier 1 achieved persists for the rest of the call).
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I'd like a new chequebook", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})  # 1st request

    for _ in range(2):  # 2nd and 3rd -- DEFAULT_MAX_SERVICE_REQUESTS_PER_CALL == 3
        client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I'd like a new chequebook", "Confidence": "0.9"})
        client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})

    client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "I'd like a new chequebook", "Confidence": "0.9"})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "yes", "Confidence": "0.9"})
    assert _has_hangup(res.text)
    assert len(app_module._banking.list_service_requests("CIF1001")) == 3
