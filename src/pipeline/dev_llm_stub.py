"""Dev-only stand-in for the Anthropic client used by `generate-narratives`.

Implements the `.messages.create()` surface that `generate_narrative()`
(`src/pipeline/generator.py`) calls, so the CLI can exercise the full worker
pipeline — routing, health scoring, narrative persistence — against a local
Supabase instance with no `ANTHROPIC_API_KEY` and no outbound network call.

Selected only at the CLI layer in `src/worker.py` (`STUB_LLM=true` env var or
`--stub-llm` flag on `generate-narratives`); `generate_narrative()` itself is
unmodified and has no knowledge this client isn't real.

Never wire `scripts/audit_narratives.py` at this client's output. The audit
harness grades narrative quality against a cross-vendor model; the text below
has no quality to measure, and scoring it would poison the `narrative_audits`
history with meaningless results.
"""

import json

import anthropic

CANNED_NARRATIVE = (
    "[STUB-LLM] This narrative was produced by the local development stub, not a "
    "real model call. Set ANTHROPIC_API_KEY and drop --stub-llm / STUB_LLM to see real output."
)

# Deliberately the minimum of the valid 1-100 range — implausible for a real narrative,
# distinct from any genuine account's sentiment, and still passes the clamp in generator.py.
CANNED_SENTIMENT = 1


class StubAnthropicClient:
    """Drop-in replacement for `anthropic.Anthropic()` that never leaves the machine."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, *, model: str = "stub-llm", **_kwargs: object) -> anthropic.types.Message:
        payload = {
            "narrative": CANNED_NARRATIVE,
            "sentiment": CANNED_SENTIMENT,
            "notable_events": [],
            "risks": [],
            "opportunities": [],
            "suggested_next_action": None,
        }
        return anthropic.types.Message(
            id="msg_stub_llm",
            type="message",
            role="assistant",
            model=model,
            content=[anthropic.types.TextBlock(type="text", text=json.dumps(payload))],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=anthropic.types.Usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=0),
        )
