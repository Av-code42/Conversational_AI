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
from ivr.telephony import app as app_module
from ivr.telephony.session_store import SessionStore

ASHA_MOBILE = "9876500001"  # mpin 4321
VIKRAM_MOBILE = "9876500002"  # mpin 1111
MEERA_MOBILE = "9876500003"  # watchlist-flagged account


@pytest.fixture(autouse=True)
def isolated_app_state(monkeypatch):
    """app.py holds module-level singleton CBS/OTP/session state, same as a
    real running service would -- reset it before every test so MPIN-fail
    counts, OTP codes, and sessions from one test never leak into another."""
    monkeypatch.setattr(app_module, "_cbs", DummyCBSClient())
    monkeypatch.setattr(app_module, "_otp_gateway", DummyOTPGateway())
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


def test_missing_call_sid_on_incoming_is_rejected(client):
    res = client.post("/voice/incoming", data={"From": ASHA_MOBILE})
    assert res.status_code == 400


def test_full_tier1_cli_matched_happy_path(client):
    client.post("/voice/incoming", data={"CallSid": "CA1", "From": ASHA_MOBILE})
    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "what's my balance", "Confidence": "0.9"})
    assert "MPIN" in _say_text(res.text)

    res = client.post("/voice/gather", data={"CallSid": "CA1", "SpeechResult": "four three two one", "Confidence": "0.9"})
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
