"""Builds the intent classifier CoordinatorFlow should use: Groq-backed LLM
classification with a keyword-matching fallback if GROQ_API_KEY is set,
keyword-only otherwise. Shared by the telephony app and the CLI harness so
both behave consistently rather than each deciding independently.
"""

from __future__ import annotations

import logging

from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator import llm_config
from ivr.coordinator.fallback_intent_classifier import FallbackIntentClassifier
from ivr.coordinator.intent_classifier import IntentClassifier, KeywordIntentClassifier
from ivr.coordinator.llm_intent_classifier import GroqChatClient, LLMIntentClassifier

logger = logging.getLogger("ivr.coordinator.build_intent_classifier")


def build_intent_classifier(tier_map: IntentTierMap) -> IntentClassifier:
    keyword_classifier = KeywordIntentClassifier(tier_map)
    if not llm_config.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set -- using keyword-only intent classification")
        return keyword_classifier

    chat_client = GroqChatClient(llm_config.GROQ_API_KEY, llm_config.GROQ_MODEL)
    llm_classifier = LLMIntentClassifier(tier_map, chat_client)
    return FallbackIntentClassifier(primary=llm_classifier, fallback=keyword_classifier)
