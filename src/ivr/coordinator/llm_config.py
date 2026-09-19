"""LLM settings for intent classification, read from environment variables.

Get a free API key at https://console.groq.com. Kept separate from
telephony/config.py since this isn't telephony-specific -- the CLI harness
uses it too.
"""

from __future__ import annotations

import os

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
