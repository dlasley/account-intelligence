# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Quick Start

```bash
# Install (one-time)
cd frontend && npm install && cd ..
uv sync

# Run
cd frontend && npm run dev            # Next.js dev server (frontend/)
uv run python -m src.worker serve     # Worker HTTP server (webhooks/API), port 8080
# other subcommands: ingest-fixtures, process-fixtures, generate-narratives, synthesise-fixtures
# (bare `python -m src.worker` only prints help)

# Test
cd frontend && npm test               # Vitest (frontend)
uv run pytest                         # pytest (root)

# Lint / format / type-check
cd frontend && npm run lint           # eslint
uv run ruff check .                   # lint
uv run ruff format .                  # format
uv run pyright                        # Python type check

# Single test
uv run pytest tests/test_worker.py::test_worker_ingest_fixtures -v
cd frontend && npm test -- path/to/file.test.ts
```

## Project Overview

An AI-native account-intelligence tool for early-stage AI/SaaS teams — founders, AEs, PMs, CSMs — who need to know what's happening with every account without piecing it together by hand. Customer Success and Account Management incumbents require configuration before they deliver anything, and typically bolt AI onto a rules-engine core. This platform inverts that: it reads the communications that already exist and produces narrative account-health assessments from day one, zero configuration. The wedge is account intelligence from raw signal; contextual outreach drafting and synthesis follow as history accumulates.

## Architecture

Two deployables, one repo:

- **Python worker** at repo root — installable package `src/` (`[tool.hatch.build.targets.wheel]` in [pyproject.toml](pyproject.toml)), entrypoint [src/worker.py](src/worker.py). Deploys to GCP Cloud Run.
- **Next.js 15 + React 19 frontend** at [frontend/](frontend/) — App Router, TypeScript strict, path alias `@/* → ./src/*`. Deploys to Vercel.
- **Supabase** for auth/DB — migrations in [supabase/](supabase/).

Full data-flow (inbound email → routing → narrative generation → audit) in [README.md](README.md) and [docs/architecture.md](docs/architecture.md).

## Key Files

- [src/](src/) — Python worker package (`src.*`): `domain/` models, `db/` layer, `config/`, `pipeline/`, `server/` (HTTP), `signals/` (inbound source adapters), `integrations/` (Plain/Pylon/Granola + credential crypto), `synthetic/` (fixture generator), `simulator/` (trajectory backfill), `observability/`.
- [tests/](tests/) — pytest (`testpaths = ["tests"]`); Hypothesis property tests in `test_invariants.py`.
- [scripts/](scripts/) — standalone CLIs: `audit_narratives.py` (cross-vendor audit harness), `simulate_history.py` (trajectory simulator), `validate_per_week.py` (fast per-week regression check), baseline capture/check, `reanchor_demo_data.py` (demo-data freshening).
- [fixtures/synthetic-scenarios/](fixtures/synthetic-scenarios/) — YAML scenarios driving the synthetic generator.
- [frontend/src/app/](frontend/src/app/) — Next.js App Router pages/layouts.
- [supabase/](supabase/) — SQL migrations.
- [docs/](docs/) — architecture + design docs (see [§Design docs](#design-docs)).

## Environment

Copy [.env.example](.env.example) to `.env`. Never commit `.env` (gitignored; settings deny-list blocks edits).

- **LLM providers**: `ANTHROPIC_API_KEY` (narrative generation; outreach uses templates, no LLM); `OPENAI_API_KEY` (audit harness — GPT-5-mini as cross-vendor auditor).
- **Supabase (worker)**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ACCESS_TOKEN` (MCP).
- **Supabase (frontend)**: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- **Inbound email**: `INBOUND_DOMAIN` (public-repo files use `signal.example.com` as the RFC-2606 placeholder).
- **Outreach + API**: `SENDGRID_API_KEY`, `CORS_ORIGINS`, `WEBHOOK_SECRET`, `SCHEDULER_SECRET`.
- **Integrations**: `INTEGRATION_ENCRYPTION_KEY` (required — encrypts Plain/Pylon/Granola credentials, ADR-020).
- **Analytics**: `POSTHOG_API_KEY`, `POSTHOG_HOST`, `POSTHOG_ENABLED`.

## Tooling Notes

- **Python** `>=3.11`. Ruff handles linting + formatting — 100-character lines, targeting 3.11, enforcing style, import order, and a set of bug-catching and modern-syntax rules (`frontend/` excluded). Pyright runs in basic mode across `src/` + `tests/`; `src/db/` suppresses Supabase JSON-union diagnostics via `executionEnvironments`. Keep new `src/` code at zero pyright errors.
- **pytest** `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed). Hypothesis property tests run 500 examples per `@given`.
- **OpenAI SDK** pinned `>=2.0,<3`, used only by the audit harness; uses `max_completion_tokens` (GPT-5 rejects `max_tokens`), `reasoning_effort: "low"`, `response_format: json_schema`.
- **Frontend**: npm only (no pnpm/yarn/bun). Vitest + jsdom.

## Common Patterns

**Domain models** (`src/domain/`): pure Python, no I/O. Immutable entities `@dataclass(frozen=True)`; mutable ones plain `@dataclass`. Enum-like fields are `StrEnum` (values match DB CHECK constraints exactly). UUIDs are `uuid.UUID`; timestamps tz-aware. Never import `src.db` from `src.domain`.

**Config** (`src/config/`): `load_config(workspace_slug)` deep-merges workspace overrides on `config/defaults.json`; cached with `@functools.cache`.

**DB layer** (`src/db/`): one module per table, typed functions (`upsert_*`, `get_*`, `insert_*`). All writes pass `workspace_id` as a column value (defense-in-depth). Row dicts ↔ domain objects via private `_from_row`/`_to_dict`.

**Pipeline** (`src/pipeline/`): `router.py` is a pure function (no DB, unit-testable). `confidence.py` is pure — engagement health is **deterministic** (signal count + window + contact diversity), not LLM-decided. `generator.py` calls Claude with prompt caching (static system block, per-account user block); the LLM returns `sentiment: int` alongside the narrative. Prompt content is A/B-gated via a per-account-sticky PostHog flag (`_resolve_prompt_variant` → v1/v2, defaults v1); see [ADR-023](docs/adr/). `health.py`'s `compute_overall_health` is a pure weighted average. Post-narrative scoring (`_score_and_snapshot`) is fire-and-log — never fail generation because scoring failed.

**Health dimensions** (`src/db/dimension_configs.py`, `dimension_scores.py`, `health_snapshots.py`): `health_dimension_configs` is a mutable config table; `account_dimension_scores` + `account_health_snapshots` are append-only with a supersede pattern. `accounts.overall_health_score` is a denormalized cache of the latest snapshot. Four dimension types today: `email`, `product_usage`, `sentiment`, `csm_score` — see `config/defaults.json` for current weights.

**Migrations**: baseline `20260423_000001_initial_schema.sql`; changes are new numbered files. Since migration 000027 (ADR-019, "Single Mutation Surface"), `authenticated` is SELECT-only on every table — new tables get `GRANT SELECT ON <table> TO authenticated;` (or a column-restricted `GRANT SELECT (col, col, ...) ON <table> TO authenticated;` when a sensitive column like an encrypted secret must stay service-role-only, per `external_credentials` in migration 000029) plus `GRANT ALL ON <table> TO service_role;`. All mutations route through a SECURITY DEFINER RPC (see the Frontend mutation pattern below) — never grant INSERT/UPDATE/DELETE to `authenticated`, never grant anything to `anon`. RLS uses `current_user_workspace_id()` (SECURITY DEFINER); every workspace-isolation policy carries both `USING` and `WITH CHECK`.

**Timestamp convention**: mutable tables use `created_at` + trigger-maintained `updated_at`. Append-only tables drop `created_at` for a column named after the event (`generated_at`, `scored_at`, `computed_at`, `received_at`, `occurred_at`, `audited_at`) and supersede via `superseded_at` instead of `deleted_at`. `signals` is the edge case: mutable (`created_at`/`updated_at`) plus a separate `occurred_at` for event time.

**Soft delete**: every mutable table has `deleted_at timestamptz NULL` (including config tables — FK-integrity + SOC 2 / GDPR two-stage erasure). Append-only tables use `superseded_at` instead.

**Tests**: fixture paths are `Path("fixtures/...")` relative to repo root. Count-based assertions are intentional regression guards. DB-dependent code is tested by patching at the call site, not by mocking the client object.

**Frontend Supabase clients** (`frontend/src/lib/supabase/`): `client.ts` (browser, `'use client'`), `server.ts` (Server Components / Route Handlers); never cross them. Middleware refreshes sessions inline.

**Frontend auth**: three sign-in modes on `/login` — Google OAuth (`signInWithOAuth`), email+password (`signInWithPassword`), magic-link OTP (`signInWithOtp`). Middleware guards all routes except `/login` and `/auth/*`. Users need a `users` row with the correct `workspace_id` for RLS.

**Frontend mutation pattern**: all writes go through SECURITY DEFINER RPCs (`supabase.rpc(...)`); direct table mutations are revoked for `authenticated` (migration 000027). Reads use `supabase.from(...).select(...)`. Business logic + workspace checks live in the RPC body. Tests mock `supabase.rpc(...)`.

**Outreach draft greeting sync** (`frontend/src/components/OutreachTab.tsx`): the greeting tracks the selected recipient via a client-side anchored swap, not a server re-render — edit-preserving because a rewritten greeting makes the swap a no-op. See the inline comments around `lastAppliedNameContactIdRef` in that file for the two invariants that keep it from desyncing across a "No recipient" hop.

**HTTP server** (`src/server/`): FastAPI app factory in `app.py`, route handlers in `routes/`, lazy imports. `GET /health`. CORS opt-in via `CORS_ORIGINS`. Webhook (`POST /inbound`) authed via `?token=<WEBHOOK_SECRET>` with `hmac.compare_digest`, fail-closed; returns 200 on permanent routing failures so SendGrid doesn't retry forever. Scheduler (`POST /run-narratives`) authed via `Authorization: Bearer <SCHEDULER_SECRET>`. Product telemetry (`POST /event`) authed via API-key Bearer against `api_keys`, with per-instance rate limiting. `POST /run-polls` (Granola poller, same `SCHEDULER_SECRET` auth) and `POST /signal/{kind}` (Plain/Pylon ticket push, HMAC-signed) round out the structured-signal integrations — see [docs/architecture.md](docs/architecture.md) § Structured Signal Integrations.

**Synthetic data generator** (`src/synthetic/`): YAML scenario authoring → per-modality generators → conversion/emission → orchestrator. Generators are pure + seeded (deterministic `uuid5` IDs; workspace ids are `uuid5(NAMESPACE_DNS, slug)`); no `datetime.now()`. Always routes through production normalisation — never bypass it. Synthesise via `uv run python -m src.worker synthesise-fixtures --scenario <path>.yaml`.

**Trajectory simulator** (`src/simulator/`, CLI `scripts/simulate_history.py`): backfills per-week historical narratives from YAML specs at `fixtures/synthetic-scenarios/trajectory.<workspace-slug>.yaml`, replaying synthesised signals through the **production** pipeline with `now_anchor=week_start`. Two invariants the module docstrings state: it must never import `src.db.*` directly, and per-week generation is its only mode — current-snapshot narratives belong to the production scheduler. `scripts/validate_per_week.py` is the cheap targeted alternative for re-auditing a few accounts after a prompt edit.

**Audit harness** (`scripts/audit_narratives.py`): a cross-vendor evaluator that grades generated narratives on 5 criteria (faithfulness, coverage, calibration, hallucination, tone-fit) using OpenAI GPT-5-mini (cross-vendor for training-priors independence). Two append-only tables (`narrative_audits`, `narrative_audit_runs`), committed atomically. Any single hard-gate failure blocks a PR. See [ADR-016](docs/adr/).

**LLM observability** (`src/observability/llm.py`): OTel instrumentation auto-captures `$ai_generation` events in PostHog for both the Claude and audit paths. `src/analytics.py` covers business-level product events + `$ai_evaluation` (one per audit criterion). Both are application telemetry, distinct from the product domain-event model (`signals`, `narratives`, etc.). Prompt/response **content** capture is hardcoded off at startup (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`), overriding any env-var value — a deliberate compliance control guarded by `tests/test_observability_content_capture.py`. See [docs/debugging-llm-output.md](docs/debugging-llm-output.md) before changing it.

### SECURITY DEFINER discipline

Forward-looking rules for any `SECURITY DEFINER` Postgres function (RPC, RLS helper). Each prevents a class of bug that looks correct on first read but fails in edge cases; code review should walk each one explicitly.

1. **Wrap nullable subquery gates in `COALESCE(..., false)`.** `IF NOT (SELECT is_super_user FROM users WHERE id = auth.uid())` has a NULL fall-through — a NULL `auth.uid()` yields NULL, which PL/pgSQL treats as false, silently passing the gate. Wrap the subquery in `COALESCE(..., false)`.
2. **Qualify columns when `RETURNS TABLE(...)` OUT params share names with table columns.** `RETURNS TABLE(id uuid, ...)` shadows `users.id`; a bare `WHERE id = auth.uid()` is ambiguous. Always qualify (`WHERE users.id = ...`).
3. **Prefer `current_user_workspace_id()` over caller-supplied `workspace_id`** for authorization — a caller can pass any ID. Reserve `workspace_id` params for deliberately cross-workspace admin functions, gated on `is_super_user`.
4. **Prefer SQL functions over PL/pgSQL** when no procedural logic is needed (avoids the OUT-param scoping traps).
5. **`GRANT EXECUTE` to `authenticated`, `REVOKE EXECUTE` from `anon`** for any super-user-gated function (belt-and-suspenders behind the in-body gate).

### LLM-output discipline

When a change produces or modifies LLM output (narrative generation, audit harness, prompt edits, synthetic generators feeding either path):

1. **Audit-pass rate is the exit criterion**, not implementation completion. A phase isn't done until an audit run on a representative slice hits the pass threshold (≥10/12 for the demo corpus).
2. **Prompt-touching commits reference a recent audit run** (date, pass count, hard-gate failures) in the commit body.
3. **Signal density is part of the prompt's input** — a prompt that passes at one corpus density is untested at another; audit at the new density before shipping.
4. **Cheapest-validation-first** — the cheapest invalidation test for an LLM-output assumption (usually a single audit run at the assumed density) runs before downstream work.
5. **Validate the validator** — an audit run producing a mass-failure signal gets a sanity check against a known-good baseline before it drives a decision.

## Design docs

- [docs/architecture.md](docs/architecture.md) — system architecture + data flow.
- [docs/signal-routing.md](docs/signal-routing.md) — the inbound-email routing cascade.
- [docs/debugging-llm-output.md](docs/debugging-llm-output.md) — how to temporarily enable prompt/response content capture for debugging. Content capture is hardcoded **off** in `src/observability/llm.py` (a compliance control) and a CI test enforces it; read this doc before touching that override.
- [docs/adr/](docs/adr/) — Architecture Decision Records (curated public set; e.g. the cross-vendor audit harness, the prompt-variant flag gating).

## Deployment

Frontend → Vercel. Python worker → GCP Cloud Run (min-instances 0, port 8080). Cloud Scheduler triggers `POST /run-narratives` every 15 minutes. Operational specifics (URLs, project IDs) are kept out of the repo.
