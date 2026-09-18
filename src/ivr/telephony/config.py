"""Telephony service settings, read from environment variables.

Get these from https://console.twilio.com (Account SID / Auth Token on the
dashboard) once you've created a free trial account and a phone number.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TWILIO_AUTH_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")

# SECURITY: this defaults to OFF. Twilio signs every webhook request with
# X-Twilio-Signature, computed against the exact public URL it thinks it
# called -- which only matches what this app sees if PUBLIC_BASE_URL below
# is set correctly for wherever you're actually running (an ngrok tunnel,
# a Codespaces forwarded port, a real deployment). Getting that URL wrong
# makes every call fail signature validation with a confusing 403, which is
# exactly the kind of thing that blocks a first end-to-end test. Validation
# is implemented and ready (see app.py's verify_twilio_signature) -- turn
# this on with TWILIO_VALIDATE_SIGNATURES=true once PUBLIC_BASE_URL is set
# correctly, and treat it as a hard requirement before any real deployment
# (without it, anyone can POST fake call events to these webhooks).
TWILIO_VALIDATE_SIGNATURES: bool = _env_bool("TWILIO_VALIDATE_SIGNATURES", False)

# The exact public base URL Twilio is configured to call, e.g.
# "https://your-codespace-8000.app.github.dev" or an ngrok URL. Required
# only if TWILIO_VALIDATE_SIGNATURES is true.
PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "")

# Phone number to <Dial> on escalation, e.g. "+911234567890". If unset, an
# escalation just speaks the escalation prompt and hangs up -- there's no
# real CSR queue to transfer to in this dev setup.
CSR_TRANSFER_NUMBER: str = os.environ.get("CSR_TRANSFER_NUMBER", "")
