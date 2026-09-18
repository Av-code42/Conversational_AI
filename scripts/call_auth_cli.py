#!/usr/bin/env python3
"""Interactive CLI harness for AuthFlow -- type responses instead of
speaking them, "fake SMS" printed to the console instead of a real OTP
gateway. No telephony, ASR, or TTS involved; this only exercises the
decision logic in src/ivr/auth/flow.py, backed by the same dummy CBS/OTP
data the test suite uses.

Run from the repo root:
    python scripts/call_auth_cli.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ivr.auth.cbs_dummy import DummyCBSClient  # noqa: E402
from ivr.auth.flow import AuthFlow  # noqa: E402
from ivr.auth.models import AuthStatus  # noqa: E402
from ivr.auth.otp_dummy import DummyOTPGateway  # noqa: E402
from ivr.auth.tier_config import IntentTierMap  # noqa: E402

TERMINAL_STATUSES = {AuthStatus.AUTHENTICATED, AuthStatus.ESCALATED, AuthStatus.NO_AUTH_REQUIRED}


def print_customer_directory(cbs: DummyCBSClient) -> None:
    print("\n--- Dummy customer directory (use these values as input) ---")
    for customer in cbs.list_customers():
        mobiles = ", ".join(customer.registered_mobiles) or "(none registered)"
        extras = []
        if customer.mpin_locked:
            extras.append("MPIN LOCKED")
        if customer.account_flags:
            extras.append(f"flags: {', '.join(f.value for f in customer.account_flags)}")
        extra_str = f"  [{'; '.join(extras)}]" if extras else ""
        print(
            f"  {customer.cif}: {customer.name} | mobile: {mobiles} | "
            f"MPIN: {customer.mpin} | account last6: {customer.account_last6} | "
            f"card last4: {customer.card_last4}{extra_str}"
        )
    print("--------------------------------------------------------------\n")


def print_intent_catalog(tier_map: IntentTierMap) -> None:
    print("\n--- Intents (from config/intent_tier_map.yaml) ---")
    for intent_id, agent_id, tier in sorted(tier_map.all_intents()):
        print(f"  {intent_id}  [{agent_id}, {tier.value}]")
    print("----------------------------------------------------\n")


def prompt_input(label: str) -> tuple[str, float]:
    """Returns (text, confidence). Prefix your answer with '~' to simulate
    a low-confidence ASR capture, e.g. '~4321'."""
    raw = input(f"{label}> ").strip()
    if raw.startswith("~"):
        return raw[1:].strip(), 0.2
    return raw, 1.0


def run_call(cbs: DummyCBSClient, otp_gateway: DummyOTPGateway, tier_map: IntentTierMap,
             intent_id: str, ani: str | None) -> None:
    flow = AuthFlow(cbs=cbs, otp_gateway=otp_gateway, tier_map=tier_map)
    state = flow.start(ani=ani, intent_id=intent_id)
    pending = state.status  # sticky across NEEDS_REPEAT loops -- see below

    while state.status not in TERMINAL_STATUSES:
        if state.status != AuthStatus.NEEDS_REPEAT:
            pending = state.status
        if state.prompt:
            print(f"\nAgent: {state.prompt}")

        if pending == AuthStatus.NEEDS_IDENTIFICATION:
            account_last6, conf_a = prompt_input("Account last 6 digits")
            card_last4, conf_b = prompt_input("Card last 4 digits")
            state = flow.submit_identification(account_last6, card_last4, confidence=min(conf_a, conf_b))

        elif pending == AuthStatus.NEEDS_MPIN:
            mpin, confidence = prompt_input("MPIN")
            state = flow.submit_mpin(mpin, confidence=confidence)

        elif pending == AuthStatus.NEEDS_OTP:
            if state.cif:
                mobile = cbs.get_registered_mobile(state.cif)
                if mobile:
                    try:
                        code = otp_gateway.last_code_sent_to(mobile)
                        print(f"[DEV] (fake SMS to {mobile}): Your one-time code is {code}")
                    except LookupError:
                        pass
            otp, confidence = prompt_input("OTP (or type 'resend')")
            if otp.lower() == "resend":
                state = flow.request_otp_resend()
                continue
            state = flow.submit_otp(otp, confidence=confidence)

        else:
            raise AssertionError(f"unhandled status {pending}")  # should never happen

    print(f"\n=== Call ended: {state.status.value} ===")
    if state.status == AuthStatus.AUTHENTICATED:
        print(f"Authenticated at {state.tier_achieved.value}. CIF={state.cif}")
    elif state.status == AuthStatus.ESCALATED:
        print(f"Escalated to CSR. Internal reason: {state.escalation_reason.value}")
        print(f"(What the caller actually heard): {state.prompt}")
    else:
        print("No authentication was required for this intent.")


def main() -> None:
    cbs = DummyCBSClient()
    otp_gateway = DummyOTPGateway()
    tier_map = IntentTierMap.load()

    print("=== IVR Authentication -- interactive CLI harness ===")
    print("No telephony/ASR/TTS here -- this drives AuthFlow directly, turn by turn.")
    print("Prefix any answer with '~' to simulate low ASR confidence, e.g. '~4321'.")

    print_customer_directory(cbs)
    print_intent_catalog(tier_map)

    while True:
        intent_id = input("\nIntent id to call about ('list' for intents, 'quit' to exit): ").strip()
        if intent_id == "quit":
            break
        if intent_id == "list":
            print_intent_catalog(tier_map)
            continue
        if intent_id not in tier_map:
            print("Unknown intent id -- type 'list' to see valid ones.")
            continue

        ani = input("Calling from (mobile number, or blank for no caller ID): ").strip() or None
        run_call(cbs, otp_gateway, tier_map, intent_id, ani)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")
