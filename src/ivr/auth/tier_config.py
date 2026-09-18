"""Loads config/intent_tier_map.yaml -- the source of truth for which
agent owns an intent and what authentication tier it requires (see
docs/design/authentication-flow.md SS4 and docs/design/agent-architecture.md
SS3 step 2, which treats this same file as the routing table).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ivr.auth.models import Tier

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "intent_tier_map.yaml"


class UnknownIntentError(KeyError):
    pass


class IntentTierMap:
    def __init__(self, mapping: dict[str, tuple[str, Tier]]):
        self._mapping = mapping

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "IntentTierMap":
        data = yaml.safe_load(path.read_text())
        mapping: dict[str, tuple[str, Tier]] = {}
        for agent_id, agent in data["agents"].items():
            for intent in agent["intents"]:
                mapping[intent["id"]] = (agent_id, Tier(intent["tier"]))
        return cls(mapping)

    def tier_for(self, intent_id: str) -> Tier:
        try:
            return self._mapping[intent_id][1]
        except KeyError:
            raise UnknownIntentError(intent_id) from None

    def agent_for(self, intent_id: str) -> str:
        try:
            return self._mapping[intent_id][0]
        except KeyError:
            raise UnknownIntentError(intent_id) from None

    def __contains__(self, intent_id: str) -> bool:
        return intent_id in self._mapping
