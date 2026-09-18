#!/usr/bin/env python3
"""Interactive CLI harness for CoordinatorFlow (greeting -> intent capture ->
AuthFlow's auth gate -> handoff -> "anything else?" loop) -- type responses
instead of speaking them, "fake SMS" printed to the console instead of a
real OTP gateway. No telephony, ASR, TTS, or real NLU involved: intent
capture uses the placeholder KeywordIntentClassifier, and there's no real
domain agent to actually run a tool once handed off -- this simulates that
completing instantly so the "anything else?" loop is exercisable.

Run from the repo root:
    python scripts/call_auth_cli.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ivr.auth.cbs_dummy import DummyCBSClient  # noqa: E402
from ivr.auth.otp_dummy import DummyOTPGateway  # noqa: E402
from ivr.auth.tier_config import IntentTierMap  # noqa: E402
from ivr.coordinator.flow import CoordinatorFlow, CoordinatorStatus  # noqa: E402
from ivr.coordinator.intent_classifier import KeywordIntentClassifier  # noqa: E402


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
    print("\n--- Intents the (placeholder) intent classifier knows about ---")
    for intent_id, agent_id, tier in sorted(tier_map.all_intents()):
        print(f"  {intent_id}  [{agent_id}, {tier.value}]")
    print("(You can just say things naturally -- e.g. \"what's my balance\" -- ")
    print(" this doesn't require typing the exact intent id.)")
    print("------------------------------------------------------------------\n")


def prompt_input(label: str) -> tuple[str, float]:
    """Returns (text, confidence). Prefix your answer with '~' to simulate
    a low-confidence ASR capture, e.g. '~4321'."""
    raw = input(f"{label}> ").strip()
    if raw.startswith("~"):
        return raw[1:].strip(), 0.2
    return raw, 1.0


def run_call(cbs: DummyCBSClient, otp_gateway: DummyOTPGateway, coordinator: CoordinatorFlow, ani: str | None) -> None:
    state = coordinator.start(ani=ani)

    while True:
        if state.prompt:
            print(f"\nAgent: {state.prompt}")

        if state.status == CoordinatorStatus.NEEDS_INTENT:
            text, confidence = prompt_input("You")
            state = coordinator.submit_utterance(text, confidence=confidence)

        elif state.status == CoordinatorStatus.NEEDS_IDENTIFICATION:
            account_last6, conf_a = prompt_input("Account last 6 digits")
            card_last4, conf_b = prompt_input("Card last 4 digits")
            state = coordinator.submit_identification(account_last6, card_last4, confidence=min(conf_a, conf_b))

        elif state.status == CoordinatorStatus.NEEDS_MPIN:
            mpin, confidence = prompt_input("MPIN")
            state = coordinator.submit_mpin(mpin, confidence=confidence)

        elif state.status == CoordinatorStatus.NEEDS_OTP:
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
                state = coordinator.request_otp_resend()
                continue
            state = coordinator.submit_otp(otp, confidence=confidence)

        elif state.status == CoordinatorStatus.READY_FOR_HANDOFF:
            print(
                f"\n>>> Routed to {state.agent_id} for '{state.intent_id}' "
                f"(authenticated at {state.tier_achieved.value}). "
                f"No real domain agent exists yet, so simulating it completing instantly. <<<"
            )
            again = input("Press Enter to continue the call, or type 'quit' to end it: ").strip()
            if again.lower() == "quit":
                print("\n=== Call ended by caller ===")
                return
            state = coordinator.mark_task_completed()

        elif state.status == CoordinatorStatus.ESCALATED:
            print(f"\n=== Call ended: escalated to CSR ===")
            print(f"Internal reason: {state.escalation_reason.value}"
                  + (f" ({state.auth_escalation_reason.value})" if state.auth_escalation_reason else ""))
            return

        elif state.status == CoordinatorStatus.CALL_ENDED:
            print("\n=== Call ended: caller said they were done ===")
            return

        else:
            raise AssertionError(f"unhandled status {state.status}")


def main() -> None:
    cbs = DummyCBSClient()
    otp_gateway = DummyOTPGateway()
    tier_map = IntentTierMap.load()
    intent_classifier = KeywordIntentClassifier(tier_map)

    print("=== ABC Retail Bank IVR -- interactive CLI harness ===")
    print("No telephony/ASR/TTS/real-NLU here -- CoordinatorFlow driven directly, turn by turn.")
    print("Prefix any answer with '~' to simulate low ASR confidence, e.g. '~4321'.")
    print("Say things like 'agent', 'repeat', or 'start over' at the intent prompt to test global commands.")

    print_customer_directory(cbs)
    print_intent_catalog(tier_map)

    while True:
        ani = input("\nNew call -- calling from (mobile number, or blank for no caller ID), 'quit' to exit: ").strip()
        if ani.lower() == "quit":
            break
        coordinator = CoordinatorFlow(cbs=cbs, otp_gateway=otp_gateway, tier_map=tier_map, intent_classifier=intent_classifier)
        run_call(cbs, otp_gateway, coordinator, ani or None)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")
