"""LLM-based intent classification via Groq, replacing the keyword-matching
placeholder (intent_classifier.KeywordIntentClassifier) with real natural-
language understanding. Behind the same IntentClassifier protocol, so
CoordinatorFlow never needs to know which classifier it's talking to.

ChatClient is its own thin protocol (not the Groq SDK directly) so
LLMIntentClassifier can be tested with a fake -- no API key, no network
call, no flakiness in the test suite. See fallback_intent_classifier.py for
what happens when the real one errors mid-call.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator.intent_classifier import DEFAULT_SYNONYMS

logger = logging.getLogger("ivr.coordinator.llm_intent_classifier")


class ChatClient(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        """Returns the raw text of the model's response."""
        ...


class GroqChatClient:
    def __init__(self, api_key: str, model: str):
        from groq import Groq  # imported lazily -- fakes shouldn't need this dependency installed

        self._client = Groq(api_key=api_key)
        self._model = model

    def complete(self, *, system: str, user: str) -> str:
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return completion.choices[0].message.content or ""


_SYSTEM_PROMPT_TEMPLATE = """You are the intent classifier for a bank's phone IVR. \
Given the caller's utterance, decide which ONE of the following intents it \
matches, if any.

Available intents:
{intent_list}

Respond with ONLY a JSON object: {{"intent_id": "<one of the ids above>"}} \
or {{"intent_id": null}} if the utterance doesn't clearly match any of them. \
Do not guess -- if genuinely ambiguous or off-topic, use null."""


def _describe_intent(intent_id: str) -> str:
    synonyms = DEFAULT_SYNONYMS.get(intent_id)
    if synonyms:
        return f"- {intent_id} (also referred to as: {', '.join(sorted(synonyms))})"
    return f"- {intent_id}"


class LLMIntentClassifier:
    def __init__(self, tier_map: IntentTierMap, chat_client: ChatClient):
        self._tier_map = tier_map
        self._chat_client = chat_client
        intent_lines = "\n".join(_describe_intent(intent_id) for intent_id, _agent, _tier in tier_map.all_intents())
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(intent_list=intent_lines)

    def classify(self, text: str) -> str | None:
        if not text.strip():
            return None

        raw = self._chat_client.complete(system=self._system_prompt, user=text)
        data = json.loads(raw)  # malformed JSON raises -- let FallbackIntentClassifier decide what to do
        intent_id = data.get("intent_id")

        if intent_id is None:
            return None
        if intent_id not in self._tier_map:
            logger.warning("LLM returned unknown intent_id %r for utterance %r", intent_id, text)
            return None
        return intent_id
