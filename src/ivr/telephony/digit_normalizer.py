"""Normalizes spoken digit sequences into digit strings, e.g.
"four three two one" -> "4321". Twilio's speech recognition often already
returns digits as digits for short numeric utterances, but not always --
this is a safety net, not the primary mechanism.

Callers don't always say *just* the digits -- e.g. "my last 6 digits for
the account is 456789" mentions a stray "6" before the real answer. Rather
than blindly concatenating every digit found anywhere in the utterance
(which would turn that example into the wrong "6456789"), this extracts
each contiguous run of digit tokens and keeps the longest one, on the
assumption that incidental numbers mentioned around the real answer
("last 6 digits", "4-digit PIN") are short compared to the actual digit
string being given. Ties keep the last run, since the real answer
typically comes after any such scene-setting phrase.

Still deliberately simple: this is a heuristic safety net, not a full NLU
parse. A wrong extraction just fails MPIN/OTP/identification validation
downstream (AuthFlow's own retry/lockout policy handles that correctly)
rather than needing special-case handling here.
"""

from __future__ import annotations

import re

_WORD_TO_DIGIT = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def normalize_spoken_digits(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if not cleaned:
        return ""

    compact = cleaned.replace(" ", "")
    if compact.isdigit():
        return compact

    runs: list[str] = []
    current: list[str] = []
    for word in cleaned.split():
        if word in _WORD_TO_DIGIT:
            current.append(_WORD_TO_DIGIT[word])
        elif word.isdigit():
            current.append(word)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))

    best = ""
    for run in runs:
        if len(run) >= len(best):
            best = run
    return best
