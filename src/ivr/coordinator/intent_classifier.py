"""Intent classification. No real NLU/LLM exists in this project yet, so
KeywordIntentClassifier is a placeholder: it matches a caller's utterance
against each intent's id (and a small synonym list) by keyword overlap.
This is not how classification should work in production -- swap it out
for real NLU/LLM classification behind the same IntentClassifier protocol
when that exists; CoordinatorFlow doesn't care which it's talking to.
"""

from __future__ import annotations

import re
from typing import Protocol

from ivr.auth.tier_config import IntentTierMap

_STOPWORDS = {
    "i", "want", "to", "my", "the", "a", "an", "please", "can", "you", "me",
    "would", "like", "get", "know", "for", "of", "is", "what", "whats",
    "on", "in", "do", "need",
}

# Hand-picked synonyms for the real catalog in config/intent_tier_map.yaml,
# since intent ids alone (e.g. "kyc_update") don't cover how someone would
# actually phrase the request. Extend as the catalog grows.
DEFAULT_SYNONYMS: dict[str, set[str]] = {
    "balance_enquiry": {"balance", "funds", "money"},
    "transaction_details": {"transaction", "transactions", "history", "spent"},
    "statement_request": {"statement", "statements"},
    "change_of_address": {"address", "move", "relocate", "relocated"},
    "cheque_book_request": {"cheque", "chequebook", "checkbook", "cheques"},
    "kyc_update": {"kyc", "verification", "reverify", "verify"},
}


class IntentClassifier(Protocol):
    def classify(self, text: str) -> str | None:
        """Returns an intent_id, or None if nothing matches confidently."""
        ...


def _keywords(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class KeywordIntentClassifier:
    def __init__(self, tier_map: IntentTierMap, *, extra_keywords: dict[str, set[str]] | None = None):
        self._catalog: dict[str, set[str]] = {}
        for intent_id, _agent_id, _tier in tier_map.all_intents():
            self._catalog[intent_id] = _keywords(intent_id.replace("_", " ")) - _STOPWORDS

        for intent_id, synonyms in DEFAULT_SYNONYMS.items():
            if intent_id in self._catalog:
                self._catalog[intent_id] |= _keywords(" ".join(synonyms)) - _STOPWORDS

        for intent_id, synonyms in (extra_keywords or {}).items():
            self._catalog.setdefault(intent_id, set())
            self._catalog[intent_id] |= _keywords(" ".join(synonyms)) - _STOPWORDS

    def classify(self, text: str) -> str | None:
        words = _keywords(text) - _STOPWORDS
        if not words:
            return None
        best_intent, best_score = None, 0
        for intent_id, keywords in self._catalog.items():
            score = len(words & keywords)
            if score > best_score:
                best_intent, best_score = intent_id, score
        return best_intent
