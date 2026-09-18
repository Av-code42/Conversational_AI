"""Normalizes spoken digit sequences into digit strings, e.g.
"four three two one" -> "4321". Twilio's speech recognition often already
returns digits as digits for short numeric utterances, but not always --
this is a safety net, not the primary mechanism.

Deliberately simple: unrecognized words are dropped rather than causing an
error, since a partially-wrong digit string will just fail MPIN/OTP
validation downstream (AuthFlow's own retry/lockout policy handles that
correctly) rather than needing special handling here.
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

    digits = []
    for word in cleaned.split():
        if word in _WORD_TO_DIGIT:
            digits.append(_WORD_TO_DIGIT[word])
        elif word.isdigit():
            digits.append(word)
    return "".join(digits)
