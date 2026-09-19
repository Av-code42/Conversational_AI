"""Wraps a primary IntentClassifier with a fallback. On a live phone call,
an LLM API error/timeout/rate-limit mid-call shouldn't take the whole
intent-capture turn down with it -- fall back to something that still
works (the keyword matcher) rather than raising all the way up to the
telephony webhook handler.
"""

from __future__ import annotations

import logging

from ivr.coordinator.intent_classifier import IntentClassifier

logger = logging.getLogger("ivr.coordinator.fallback_intent_classifier")


class FallbackIntentClassifier:
    def __init__(self, primary: IntentClassifier, fallback: IntentClassifier):
        self._primary = primary
        self._fallback = fallback

    def classify(self, text: str) -> str | None:
        try:
            return self._primary.classify(text)
        except Exception:
            logger.exception("Primary intent classifier failed for utterance %r, falling back", text)
            return self._fallback.classify(text)
