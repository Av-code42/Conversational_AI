"""Shared global-command detection.

Per docs/design/agent-architecture.md SS3 step 5 and SS8.1 (resolved: direct
handoff), this has to be one shared check every conversational turn owner
runs -- the Coordinator during its own intent-capture turns, and later every
domain agent during its own turns -- rather than reimplemented per agent.

Deliberately NOT wired into AuthFlow's factor-capture turns (MPIN/OTP/
identification digits): those are short, tightly-scoped digit strings, and
naive keyword matching against them is dangerous -- a caller's MPIN or OTP
can easily contain "zero" or "0" as a legitimate digit, which would
false-positive against an "escalate" trigger if this ran there too. Matching
requires a trigger word to be a *whole token* (or, for multi-word phrases, a
substring of the normalized text) rather than a raw substring match, which
still isn't safe enough for digit-heavy input. Only call this on natural-
language utterances (intent capture, or a domain agent's own conversational
turns) -- never on a raw digit capture.
"""

from __future__ import annotations

from enum import Enum


class GlobalCommand(str, Enum):
    ESCALATE_TO_CSR = "escalate_to_csr"
    REPEAT = "repeat"
    START_OVER = "start_over"


_ESCALATE_WORDS = {"agent", "representative", "human", "operator"}
_ESCALATE_PHRASES = {"customer service", "talk to someone", "speak to someone", "speak to a person"}

_START_OVER_WORDS = {"restart"}
_START_OVER_PHRASES = {"start over", "start again", "main menu"}

_REPEAT_WORDS = {"repeat", "pardon"}
_REPEAT_PHRASES = {"say that again", "come again", "one more time", "didn't catch that"}


def detect_global_command(text: str) -> GlobalCommand | None:
    normalized = text.strip().lower()
    if not normalized:
        return None
    words = set(normalized.split())

    if words & _ESCALATE_WORDS or any(phrase in normalized for phrase in _ESCALATE_PHRASES):
        return GlobalCommand.ESCALATE_TO_CSR
    if words & _START_OVER_WORDS or any(phrase in normalized for phrase in _START_OVER_PHRASES):
        return GlobalCommand.START_OVER
    if words & _REPEAT_WORDS or any(phrase in normalized for phrase in _REPEAT_PHRASES):
        return GlobalCommand.REPEAT
    return None


# NOT a global command -- "no"/"bye" are only safe to treat as "caller is
# done" in the specific context of having just asked "anything else?".
# Checked unconditionally at any intent-capture turn, "no" would wrongly
# end a call on something like "no idea what to do" on the very first
# turn. CoordinatorFlow only calls this right after ANYTHING_ELSE_PROMPT.
_NO_FURTHER_HELP_WORDS = {"no", "nope", "bye", "goodbye"}
_NO_FURTHER_HELP_PHRASES = {
    "no thanks", "no thank you", "that's all", "thats all", "nothing else",
    "i'm done", "im done", "that's it", "thats it", "no i'm good", "no im good",
}


def is_no_further_help_needed(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    words = set(normalized.split())
    return bool(words & _NO_FURTHER_HELP_WORDS) or any(phrase in normalized for phrase in _NO_FURTHER_HELP_PHRASES)
