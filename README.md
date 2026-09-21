# ABC Retail Bank — Conversational IVR

A voice-first, DTMF-free banking IVR: callers speak everything, including
OTP and MPIN, and a Coordinator agent routes them to one of three domain
agents once authenticated at the tier their request requires. Built as a
working prototype end to end — real Twilio telephony, real (dummy-backed)
tools, 197 automated tests, and design docs written before the code that
implements them.

```
Call → Greeting/Intent capture → Tier-gated auth → Domain agent → "Anything else?" → End/Escalate
```

See [`docs/design/call-flow-diagram.md`](docs/design/call-flow-diagram.md)
for the full flowchart of what an actual call does today.

## Why no DTMF

Every sensitive digit — MPIN, OTP, account number, card number — is spoken
and captured via ASR, never keyed in. That's a deliberate product
constraint, not a limitation: it changes the risk profile (a spoken secret
exists in an audio waveform, not a momentary tone) which is why the
compliance notes in the auth design doc exist. See
[`docs/design/authentication-flow.md`](docs/design/authentication-flow.md) §10.

## Architecture

**Coordinator Agent** — owns the greeting, intent capture (LLM-based via
Groq, with a keyword-matching fallback), the tier-gated authentication
handoff, and global commands ("agent", "repeat", "start over"). Routes to
exactly one domain agent per intent and regains control on completion.

**Three domain agents**, each owning a fixed set of tools:

| Agent | Owns (intents) | Tools |
|---|---|---|
| Accounts Agent | `balance_enquiry` | `get_balance` |
| Transaction Agent | `transaction_details`, `statement_request` | `get_recent_transactions`, `get_statement` |
| Service Agent | `change_of_address`, `cheque_book_request`, `kyc_update` | `submit_service_request` |

Full contract and design rationale in
[`docs/design/agent-architecture.md`](docs/design/agent-architecture.md).
Which agent owns which intent, and at what authentication tier, is data —
[`config/intent_tier_map.yaml`](config/intent_tier_map.yaml) — not
hardcoded, so adding an intent is a config change plus one agent method.

## Authentication

Risk-based, three tiers — no authentication for public info, a possession
+ knowledge factor for self-service, both MPIN **and** OTP for anything
sensitive. CLI (caller-ID) match is checked silently first; if it doesn't
match, the caller identifies themselves with account + card digits before
an OTP can even be sent anywhere. Full retry/lockout/escalation policy,
tier definitions, and the CBS/OTP integration contract are in
[`docs/design/authentication-flow.md`](docs/design/authentication-flow.md).

Implementation: [`src/ivr/auth/flow.py`](src/ivr/auth/flow.py) (`AuthFlow`,
a state machine), backed by dummy CBS/OTP implementations
([`cbs_dummy.py`](src/ivr/auth/cbs_dummy.py),
[`otp_dummy.py`](src/ivr/auth/otp_dummy.py)) standing in for a real core
banking integration.

## Guardrails

- **Confirm-before-commit.** Every Service Agent tool (address change,
  cheque book, KYC update) mutates a record, so all three read back what
  they're about to do and require an explicit yes before submitting —
  protection against a misheard instruction or an accidental request going
  through silently. See [`service_agent.py`](src/ivr/agents/service_agent.py).
- **Per-call rate limiting.** A `ServiceRequestLimiter`
  ([`rate_limit.py`](src/ivr/agents/rate_limit.py)) caps how many service
  requests a single call can submit, guarding against a looping or abusive
  call racking up unlimited tickets.
- **ASR confidence gating.** Below a confidence threshold, every
  digit-capture and confirmation turn soft-reprompts ("didn't catch that")
  *without* consuming a real attempt, capped before escalating — so a bad
  line doesn't get punished like a wrong answer, but an indefinite loop
  still isn't possible.
- **Retry/lockout ceilings everywhere that matters.** MPIN attempts, OTP
  verification tries, OTP resends, and account/card identification
  attempts are all capped (§7 of the auth design doc), with every
  exhaustion routing to the same escalation path rather than a dead end.
- **Anti-enumeration.** Identical, generic failure messaging whether an
  account doesn't exist or a factor was simply wrong; a flagged account
  (dormant/watchlist/blocked) escalates silently without ever revealing why.
- **No enumeration via account/card alone.** Last-4 card and last-6
  account digits are treated as an identification *pair*, never a
  standalone authentication factor.
- **Compliance-aware by design.** No DTMF anywhere (see above); no
  reading a captured secret back to "confirm" it; recording
  redaction/suppression called out for sensitive turns; DPDP Act 2023
  considerations documented — [`authentication-flow.md`](docs/design/authentication-flow.md) §10.

## Tech stack

Python 3.11 · FastAPI + Twilio (`<Gather>`/`<Say>` for ASR/TTS) · Groq
(LLM intent classification, with keyword-matching fallback) · pytest ·
protocol-based dependency injection throughout (every external
integration — CBS, OTP, banking, chat client — is a `typing.Protocol`
with a dummy implementation swapped in for now).

## Project structure

```
src/ivr/
  auth/          AuthFlow state machine + dummy CBS/OTP + tier config
  coordinator/   CoordinatorFlow, intent classification (LLM + fallback)
  agents/        AccountsAgent, TransactionAgent, ServiceAgent, registry, rate limiter
  banking/       Dummy banking client (balance, transactions, service requests)
  telephony/     FastAPI app wrapping CoordinatorFlow for real Twilio calls
  shared/        Global command detection ("agent", "repeat", "start over")
config/          intent_tier_map.yaml -- intent -> agent -> tier, source of truth
docs/design/     Design docs (auth flow, agent architecture, call-flow diagram)
scripts/         Text-based CLI harness -- drive a full call by typing, no telephony
tests/           197 tests covering every documented path
```

## Running it

### Option A — text-based CLI harness (no telephony, no Twilio account)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/call_auth_cli.py
```

Drives a full call — greeting, intent, auth, a real domain agent, "anything
else?" — by typing responses instead of speaking them. Prefix any answer
with `~` to simulate low ASR confidence (e.g. `~4321`). Set `GROQ_API_KEY`
in `.env` for real LLM intent classification; otherwise falls back to
keyword matching.

### Option B — a real phone call via Twilio

```bash
uvicorn ivr.telephony.app:app --reload --port 8000 --app-dir src
```

1. Make port 8000 publicly reachable (ngrok, or a Codespaces forwarded
   port set to fully Public).
2. Set `PUBLIC_BASE_URL` in `.env` to that public URL — required for
   `<Gather>` callbacks to resolve reliably.
3. Twilio Console → your number → Voice Configuration → "A call comes
   in" → Webhook → `{PUBLIC_BASE_URL}/voice/incoming` → HTTP POST.
4. Call the number.

See [`.env.example`](.env.example) for every config knob (Gather timing,
CSR transfer number, signature validation, Groq model).

### Tests

```bash
python -m pytest -q
```

197 tests: every path through the auth state machine (§6/§7/§8 of the
auth design doc), all three domain agents including confirm-before-commit
and rate-limiting, intent classification (keyword, LLM, and the fallback
between them), and the full Twilio webhook contract simulated end to end
via FastAPI's `TestClient` — a passing suite here means the real webhook
contract works, modulo actual ASR/TTS behavior itself.

## What's still a placeholder

- **CBS, OTP gateway, and banking data are all in-memory dummies** — no
  real core banking integration. Swapping one in only touches the relevant
  `*_dummy.py` file, since everything else depends on a `Protocol`.
- **Intent classification is LLM-based (Groq), not a hand-built NLU
  model** — good enough for this catalog's phrasing variety, not a
  production NLU claim.
- **Global commands aren't yet checked during a domain agent's own
  turns** (e.g. mid-address, mid-confirmation) — only at the Coordinator's
  top-level intent capture. Flagged, not yet built.
- **Mid-call tier step-up re-runs full authentication** instead of just
  the delta, since no Tier 2 intent exists yet to need it correctly.
- Accounts/Transaction Agent tools have **fixed defaults** (last 5
  transactions, last 30 days) rather than slot-filling for a date range —
  a deliberate v1 simplification, not a gap anyone's hit.

## Design docs

- [`docs/design/authentication-flow.md`](docs/design/authentication-flow.md) — full auth spec: tiers, factors, retry/lockout policy, escalation triggers, compliance notes.
- [`docs/design/agent-architecture.md`](docs/design/agent-architecture.md) — Coordinator/domain-agent contract, session data model, open questions.
- [`docs/design/call-flow-diagram.md`](docs/design/call-flow-diagram.md) — block/Mermaid view of what an actual call does today, end to end.
