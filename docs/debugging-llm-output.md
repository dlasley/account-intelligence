# Debugging LLM output with content capture

## Context

This codebase auto-instruments every LLM call (Anthropic narrative generation, OpenAI audit) via OpenTelemetry. Spans flow to PostHog as `$ai_generation` events with token counts, latency, cost, model, and other metadata.

**Prompt and response text are NOT captured by default.** [`src/observability/llm.py`](../src/observability/llm.py) hardcodes `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` at startup, regardless of any environment variable value. This is a SOC-2 Type II / ISO 27001-aligned control: no customer LLM content flows to a third-party observability provider unless a deliberate, reviewable code change is made.

A CI-enforced regression test ([`tests/test_observability_content_capture.py`](../tests/test_observability_content_capture.py)) verifies the content-capture override is in place on every PR. Any change that removes or weakens it fails CI.

But content capture is a real debugging capability. PostHog LLM Analytics is uniquely suited for prompt-iteration UI, side-by-side comparisons, LLM-as-judge evaluation on captured content, and post-mortem forensics. **This document is how you use it safely when you need it.**

---

## When you need this

- Iterating on a prompt and wanting to compare versions A/B in PostHog's LLM Analytics UI
- Diagnosing an unexpected audit verdict by inspecting the full prompt + response GPT-5-mini saw
- Cross-model evaluation (comparing Claude vs GPT-5 outputs on the same input)
- Running PostHog's built-in LLM-as-judge evaluations on captured content
- Forensic analysis of an LLM regression where you want spans with content

A "span" here is one OpenTelemetry unit of work — one LLM API call — which becomes one `$ai_generation` event in PostHog with all the call's metadata (and optionally its content) as event properties.

---

## Available methods

Four ways to capture LLM content for debugging, depending on what you need:

| Method | Where content goes | Best for |
|---|---|---|
| [Re-enable PostHog content capture for a session](#re-enable-posthog-content-capture-for-a-session-recommended) **(recommended)** | PostHog Cloud — full LLM Analytics UI access | Prompt-iteration, cross-model evaluation, LLM-as-judge analysis |
| [Console exporter (local stdout)](#console-exporter-local-stdout) | Your terminal | One-off "what did the LLM see?" questions |
| [JSON Lines file exporter (local persistent)](#json-lines-file-exporter-local-persistent) | A local `.jsonl` file | Larger debug sessions where you want to grep/diff offline |
| [Separate PostHog project for debugging](#separate-posthog-project-for-debugging) | A second PostHog project (yours, not production) | Frequent prompt-iteration; cleanest data isolation |

The first method is the recommended default because it's the only one that gives you the full PostHog LLM Analytics surface (UI, trace view, built-in evaluations). The other three are alternatives for narrower use cases or stricter data isolation.

---

## Re-enable PostHog content capture for a session (recommended)

This method temporarily lifts the content-capture lockdown so the next LLM calls you make are visible in PostHog's LLM Analytics UI with full prompt and response text.

### Workflow

1. **Create a feature branch** for your debugging session. Never do this on `main`.

2. **Edit [`src/observability/llm.py`](../src/observability/llm.py)** to comment out the line that hardcodes the OTel env var to `"false"`. The line looks like:

   ```python
   os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
   ```

   Replace with:

   ```python
   # Temporarily disabled for debug session — REVERT BEFORE PUSHING.
   # os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
   ```

3. **Set the env vars in your local shell only** (do NOT commit to `.env`):

   ```bash
   export POSTHOG_LLM_CAPTURE_CONTENT=true
   export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
   ```

4. **Run against a synthetic workspace ONLY.** Examples: `lattice-build`, `synth-champion`, `synth-escalation`, `synth-silence`, `seed-stage-saas`. Never run against a workspace that contains real customer data while content capture is enabled.

5. **Inspect in PostHog LLM Analytics UI** — filter `$ai_generation` events by your session's `distinct_id` (`account-intelligence-development`) and by your run's timestamp.

6. **Before pushing the branch**: revert the `src/observability/llm.py` change. The CI regression test will fail any branch that lands with the override removed — so if you forget, CI catches you. But forming the habit of reverting before pushing avoids the CI cycle.

7. **Do not merge the branch with the override removed.** Even if you intended to push for review, the CI test will block it. The override stays in for production.

### Why the CI test is the backstop

Process controls (the documented workflow above) rely on developer discipline. The CI test is a technical control: it asserts at the unit-test level that `setup_llm_observability` has forced the OTel content-capture env var to `"false"`. If you commit a version of `llm.py` with the override removed, the test fails. Any PR with a failing CI cannot merge. Production main always has the override in place.

This is the SOC-2 audit-evidence backbone: "we have a CI test that has been green for the audit period, proving the override was in place."

### What goes to PostHog (and how to audit it)

When this method is active in your local dev, content flows to whichever PostHog project's API key is in your `.env` — likely the production project, unless you've set up a separate one (see [Separate PostHog project for debugging](#separate-posthog-project-for-debugging) below for that).

For audit purposes, every span emitted is tagged at instrumentation time with:

- `workspace.slug` — `lattice-build`, `synth-champion`, etc. Synthetic workspaces have predictable slug patterns; real customer workspaces have different slugs.
- `workspace.is_synthetic` — explicit boolean, derived from the workspace's `is_synthetic` column (once the runtime workspace guard described in the [audit model](#the-audit-model-defense-in-depth) section is in place).
- `deploy_env` — `development` / `staging` / `production`. Set from the `DEPLOY_ENV` env var.

**Audit query for SOC-2 evidence**:

```sql
-- Production project, content-attribute filter
SELECT count() FROM events
WHERE event = '$ai_generation'
  AND timestamp >= '<audit_period_start>'
  AND timestamp < '<audit_period_end>'
  AND properties.deploy_env = 'production'
  AND length(toString(properties.$ai_input)) > 2
-- Expected result: 0. Production never captures content because of the override.
```

```sql
-- Any project, non-synthetic workspace + content captured
SELECT count() FROM events
WHERE event = '$ai_generation'
  AND length(toString(properties.$ai_input)) > 2
  AND properties.workspace_is_synthetic = false
-- Expected result: 0. Content capture is only permitted against synthetic workspaces.
```

The second query is the safety-boundary check. It should return 0 for all time, across all projects, in all environments.

### When this method is not appropriate

- Against a workspace with real customer data (any production pilot account). Use the [console exporter](#console-exporter-local-stdout) or [JSON Lines exporter](#json-lines-file-exporter-local-persistent) for that — they don't transfer to PostHog at all.
- For routine production debugging. Use [`audit_events`](../supabase/migrations/) and [`narrative_audits.reasoning`](../scripts/audit_narratives.py) — the production audit trail already captures audit-decision context.
- When the question is "what's the LLM's latency / cost / token distribution?" — those metrics are captured regardless of content settings. You don't need to re-enable content capture for them.

---

## Console exporter (local stdout)

For one-off "what did the LLM see?" questions where you don't need PostHog's UI:

1. Edit `src/observability/llm.py` to replace the `PostHogSpanProcessor` block with:

   ```python
   from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
   tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
   ```

2. Set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` in your shell.

3. Run. Span content prints to stdout in JSON format.

**No third-party transfer.** Best for ad-hoc questions where you want the answer in 30 seconds.

---

## JSON Lines file exporter (local persistent)

For larger debugging sessions where you want persistent grep-able output:

```python
# Replace the PostHogSpanProcessor block:
import json
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

class JsonLinesExporter(SpanExporter):
    def __init__(self, path):
        self.path = path
    def export(self, spans):
        with open(self.path, 'a') as f:
            for span in spans:
                f.write(span.to_json() + '\n')
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass

tracer_provider.add_span_processor(SimpleSpanProcessor(JsonLinesExporter('/tmp/llm-spans.jsonl')))
```

After the run: `jq '.attributes' /tmp/llm-spans.jsonl` for inspection. Best for prompt-iteration where you want to diff versions over a session.

---

## Separate PostHog project for debugging

If you do prompt-iteration as a regular practice, set up a second PostHog project so debug captures never mix with production data:

1. Create a new PostHog project in your org (named e.g., "Account Intelligence Debug")
2. Set `POSTHOG_API_KEY` in your local `.env.local` to the debug project's key (production deploys never use this key)
3. Use the [recommended method](#re-enable-posthog-content-capture-for-a-session-recommended) normally; content flows only to the debug project

**Audit story improves significantly**: the production project has zero content (proven by content-attribute filter); the debug project has only synthetic-workspace content (proven by workspace_slug tag filter).

This is on the post-push punch list as a recommended setup for the team.

---

## The audit model (defense in depth)

Three layers of control prevent inadvertent customer-PII transfer to PostHog:

| Layer | Mechanism | Failure mode prevented |
|---|---|---|
| **1. Code-level override + CI test** | `src/observability/llm.py` forces the OTel env var to `"false"` at startup. A unit test asserts the override is in place. CI blocks any PR that removes it. | Developer forgets to revert local changes before pushing. |
| **2. Separate dev PostHog project** | Operator-level: production API key is distinct from dev API key. Production never has the override removed; dev project receives only debug traffic. | Local debug session pollutes the production project. |
| **3. Runtime workspace guard** | Code-level: at the LLM call site, if content capture is enabled, assert `workspace.is_synthetic == true` before allowing the call. Refuse if false. | Developer accidentally runs the recommended method against a real-customer workspace. |

Layer 1 is in place. Layers 2 and 3 are on the post-push punch list — they harden the model further before any pilot customer signs up.

---

## What NOT to do

- Do NOT remove the override in `src/observability/llm.py` for any reason that isn't a deliberate, time-bounded debug session
- Do NOT set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` in any Cloud Run / production environment variable
- Do NOT enable content capture against a workspace that contains real customer data, ever
- Do NOT commit any change to `src/observability/llm.py` that enables content capture by default
- Do NOT bypass the CI regression test (no `pytest.mark.skip`, no test deletion). If you need to enable content capture for a debugging session, do it locally on a branch you don't merge.

---

## Cross-references

- [`src/observability/llm.py`](../src/observability/llm.py) — the OTel setup module
- [`tests/test_observability_content_capture.py`](../tests/test_observability_content_capture.py) — the CI regression test
- [PostHog LLM Analytics docs](https://posthog.com/docs/ai-engineering/llm-analytics) — third-party reference
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — span attribute spec
