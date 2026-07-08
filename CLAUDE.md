# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

```bash
# Install (one-time)
cd frontend && npm install && cd ..
uv sync

# Run
cd frontend && npm run dev            # Next.js dev server (frontend/)
uv run python -m src.worker           # Python worker (repo root)

# Test
cd frontend && npm test               # Vitest (frontend)
uv run pytest                         # pytest (root)

# Lint / format / type-check
cd frontend && npm run lint           # next lint / eslint
uv run ruff check .                   # lint
uv run ruff format .                  # format
uv run pyright                        # Python type check

# Single test
uv run pytest tests/test_worker.py::test_worker_ingest_fixtures -v
cd frontend && npm test -- path/to/file.test.ts
```

## Project Overview

This platform is an AI-native account intelligence tool for early-stage AI/SaaS teams — founders, AEs, PMs, and CSMs — who need to know what's happening with every account without spending time piecing it together. The problem incumbents don't solve: every existing tool requires configuration before it delivers anything useful, and their AI is a last-mile addition on top of a rules-engine core. This platform inverts that — it reads the communications that already exist and produces narrative account health assessments from day one, zero configuration required. The wedge is account intelligence from raw signal; contextual outreach drafting and synthesis follow as account history accumulates.

## Architecture

Two deployables, one repo:

- **Python worker** at repo root — installable package `src/` (configured via `[tool.hatch.build.targets.wheel]` in [pyproject.toml](pyproject.toml)), entrypoint [src/worker.py](src/worker.py). Deploys to GCP Cloud Run.
- **Next.js 15 + React 19 frontend** at [frontend/](frontend/) — App Router (`frontend/src/app/`), TypeScript strict mode, path alias `@/* → ./src/*`. Deploys to Vercel.
- **Supabase** for auth/DB — migrations live in [supabase/](supabase/). The [.mcp.json](.mcp.json) ships a Supabase MCP server enabled by default (needs `SUPABASE_ACCESS_TOKEN` in env).

For the full data-flow diagram (inbound email → routing → narrative generation → audit), see [README.md](README.md) and [docs/architecture.md](docs/architecture.md).

## Key Files

- [src/](src/) — Python worker package (importable as `src.*`)
- [src/synthetic/](src/synthetic/) — Synthetic data generator: scenario YAML loader, per-modality generators (email + product), orchestrator, materialise CLI (ADR-015)
- [scripts/](scripts/) — Standalone CLI scripts: `audit_narratives.py` (cross-model audit harness, ADR-016), `derive_elicit_baseline.py` (one-shot fixture-equivalence baseline derivation), `reanchor_demo_data.py` (repeatable demo-data freshening: shifts a demo workspace's signal `occurred_at` + product-body embedded dates forward so the corpus reads as recent before a demo; dry-run default, snapshot-backed)
- [tests/](tests/) — pytest tests (`testpaths = ["tests"]`)
- [tests/synthetic/](tests/synthetic/) — Synthetic-pipeline tests: orchestrator, equivalence (vs Elicit baseline), audit integration, dimension distribution
- [tests/test_invariants.py](tests/test_invariants.py) — Hypothesis property tests (overall_score, routing_confidence, uuid5 stability)
- [tests/test_audit_harness.py](tests/test_audit_harness.py) — Audit harness unit tests
- [fixtures/synthetic-scenarios/](fixtures/synthetic-scenarios/) — YAML scenarios driving the synthetic generator; the audit corpus + the elicit-baseline equivalence scenario
- [frontend/src/app/](frontend/src/app/) — Next.js App Router pages/layouts
- [supabase/](supabase/) — Supabase SQL migrations
- [.claude/](.claude/) — Claude Code configuration (skills, hooks, settings)
- [.mcp.json](.mcp.json) — MCP servers: **Supabase** (HTTP, OAuth) + **`supabase_ro`** (same project, `?read_only=true`, allowlisted in `~/.claude/settings.json` so SELECT/`get_logs`/`get_advisors` etc. run prompt-free; writes still gate through the full server). **Routing rule**: see [§Supabase MCP routing](#supabase-mcp-routing-read-only-vs-full) below — read queries → `supabase_ro`, mutations → `supabase`. Plus **Playwright** (stdio via `npx -y @playwright/mcp@latest`, for browser automation against the live frontend). The Vercel MCP comes from the global plugin layer, not `.mcp.json`; it can be authorized per-session with `mcp__plugin_vercel_vercel__authenticate`, but **deployment / log inspection won't work for this project** — Vercel's hosted MCP doesn't grant team-scope access to personal "team-of-one" projects via OAuth, and `list_teams` returns `[]` even after a clean re-auth (verified 2026-05-02). Use `vercel` CLI for deploy / log ops; the MCP is still useful for `search_vercel_documentation`.

## Environment

Copy [.env.example](.env.example) to `.env` and fill in values. Never commit `.env` — it's in `.gitignore` and the `.claude/settings.json` deny list blocks edits to it.

- **LLM providers**: `ANTHROPIC_API_KEY` (narrative generation only — outreach uses templates, no LLM); `OPENAI_API_KEY` (audit harness only — `scripts/audit_narratives.py` uses GPT-5-mini as the cross-vendor narrative auditor per ADR-016)
- **Supabase (worker)**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, plus `SUPABASE_ACCESS_TOKEN` for the MCP server
- **Supabase (frontend)**: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — same project values, `NEXT_PUBLIC_` prefix required for Next.js client bundle
- **Inbound email**: `INBOUND_DOMAIN` (e.g. `signal.yourdomain.com`; production value is set in deploy env and gitignored `.env` — public repo files use `signal.example.com` as the RFC-2606 test placeholder, never the real value)
- **Outreach send**: `SENDGRID_API_KEY` (SendGrid Transactional API key for outreach send), `CORS_ORIGINS` (comma-separated allowed origins for the worker API, e.g. `http://localhost:3000` locally or the Vercel URL in prod; unset = no CORS headers issued)

## Tooling Notes

- **Python**: `requires-python = ">=3.11"`. Ruff is configured (line-length 100, target py311, rules `E,F,I,W,B,UP,RUF`) and excludes `frontend/`.
- **Pyright** (basic mode) runs across `src/` + `tests/`. `src/db/` has Supabase-related diagnostics suppressed via `executionEnvironments` because supabase-py returns `JSON` unions that pyright cannot narrow. Baseline ≈150 errors (all in test files — `None` passed to typed mock parameters; tolerated noise). `src/` itself is zero errors. Aim to keep new `src/` code at zero errors. Run with `uv run pyright`.
- **pytest**: `asyncio_mode = "auto"` — async tests don't need `@pytest.mark.asyncio`. Test count: 813 passing.
- **Hypothesis**: `hypothesis>=6.0` is a dev dep. Property tests live in [tests/test_invariants.py](tests/test_invariants.py); they exercise production invariants (overall_score weighted-average, routing_confidence range, uuid5 ID stability) with 500 generated examples per `@given`. The `.hypothesis/` example database is gitignored.
- **OpenAI SDK**: `openai>=2.0,<3` is a runtime dep, used only by the audit harness in `scripts/audit_narratives.py`. Pinned to major-version 2 (post-1.x security advisories). The audit script uses `max_completion_tokens` (NOT `max_tokens` — GPT-5 family rejects the older parameter), `reasoning_effort: "low"`, and `response_format: json_schema` for strict structured output.
- **Frontend**: npm only (no pnpm/yarn/bun). Vitest with jsdom for unit tests.

### Schema introspection

**Schema introspection.** When you need to know the current shape of a table, function, or constraint, query the live database via the Supabase MCP (`mcp__supabase_ro__execute_sql` against `information_schema` / `pg_proc` / `pg_indexes`) — not by parsing accumulated migration files. Migration files are for *change history and design intent*; the cumulative current state is in Postgres. Example: `SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'contacts';`

### Supabase MCP routing (read-only vs full)

**Standing rule**: route all read-only Supabase queries through `mcp__supabase_ro__*` (the `?read_only=true` MCP server allowlisted in `~/.claude/settings.json` — runs prompt-free). Reserve `mcp__supabase__*` (the full server) for INSERT / UPDATE / DELETE / RPCs that mutate state, and `mcp__supabase__apply_migration` for schema migrations. The full server's calls require an approval prompt; routing through the read-only variant for SELECT-style work avoids approval friction on every diagnostic query.

| Operation | Tool | Approval? |
|---|---|---|
| `SELECT`, `information_schema`, `pg_proc`, `pg_indexes`, `get_logs`, `get_advisors`, `list_tables`, `list_migrations`, `list_extensions`, `search_docs` | `mcp__supabase_ro__*` | No (allowlisted) |
| `INSERT` / `UPDATE` / `DELETE` rows, RPCs that mutate, `deploy_edge_function`, branch ops | `mcp__supabase__*` | Yes |
| Schema migrations | `mcp__supabase__apply_migration` | Yes |

When you delegate a task to a sub-agent that touches the database, include this routing directive in the brief. The agents read CLAUDE.md at session start, but explicit per-brief reminders prevent slip-through.

### PostHog MCP routing (read-only vs full)

**Standing rule**: same shape as Supabase. PostHog MCP is at `https://mcp.posthog.com/mcp` (global plugin server, not in `.mcp.json`); authenticate per-session via `mcp__plugin_posthog_posthog__authenticate` then complete OAuth flow. Once authenticated, the namespace exposes ~250 tools across analytics, insights, dashboards, feature flags, cohorts, persons, error tracking, LLM analytics, surveys, etc.

A curated read-only subset is allowlisted in `~/.claude/settings.json` so common diagnostic queries run prompt-free. Mutations always gate.

| Operation | Tool category | Approval? |
|---|---|---|
| HogQL queries, schema introspection, docs search | `query-run`, `query-validate`, `hogql-schema`, `docs-search`, `entity-search` | No (allowlisted) |
| Read insights, dashboards, cohorts, feature flags, definitions, persons | `insights-list`, `insight-get`, `insight-query`, `dashboards-get-all`, `dashboard-get`, `cohorts-list`, `cohorts-retrieve`, `feature-flag-get-all`, `feature-flag-get-definition`, `event-definitions-list`, `properties-list`, `persons-list`, `persons-retrieve` | No (allowlisted) |
| LLM Analytics read | `get-llm-total-costs-for-project`, `llma-prompt-{list,get}`, `llma-evaluation-{list,get}`, `llma-evaluation-report-{list,get}` | No (allowlisted) |
| Error tracking read | `query-error-tracking-issue{,-events}`, `query-error-tracking-issues-list` | No (allowlisted) |
| Activity / annotations / project / org / user read | `activity-log-list`, `annotations-list`, `annotation-retrieve`, `project-get`, `projects-get`, `organization-get`, `organizations-list`, `user-get` | No (allowlisted) |
| Mutations: any `*-create`, `*-update`, `*-delete`, `*-destroy`, `*-partial-update`, `*-archive`, `*-launch`, `*-pause`, `*-resume`, `*-merge`, `*-split`, `*-materialize`, `*-set-active-key`, `*-add-persons`, `*-rm-person`, `switch-organization`, `switch-project`, etc. | Full server (gates) | Yes |

**Person-on-events note**: this project has Person-on-events mode enabled. When querying `person.properties.*` on the events table, values reflect what was set at the time the event was ingested, not the person's current value. The same person can have different property values across different events. Don't suggest workarounds for "query-time" person properties.

### SECURITY DEFINER function discipline

When writing a `SECURITY DEFINER` Postgres function (RPC, RLS helper, etc.), apply these rules. They protect against the failure modes that have actually surfaced in this codebase, not hypothetical ones — every rule below corresponds to a real bug we've fixed.

1. **Wrap nullable subquery gates in `COALESCE(..., false)`.** A gate like `IF NOT (SELECT is_super_user FROM users WHERE users.id = auth.uid()) THEN RAISE EXCEPTION ...` looks correct but has a NULL fall-through. If `auth.uid()` is NULL (anon caller, or transient signup state with no `users` row yet), the subquery returns NULL → `NOT NULL = NULL` → PL/pgSQL treats NULL as false → gate silently passes and runs privileged code. Fix:
   ```sql
   IF NOT COALESCE(
       (SELECT is_super_user FROM users WHERE users.id = auth.uid()),
       false
   ) THEN RAISE EXCEPTION 'access denied';
   END IF;
   ```
   Bug history: caught in commit `750b006` migration 000026 RPCs; backported to migration 000025's `list_all_workspaces_with_metadata` after a defense-in-depth review.

2. **Qualify column references when `RETURNS TABLE(...)` OUT parameters share names with table columns.** `RETURNS TABLE(id uuid, ...)` declares `id` as an OUT parameter that shadows `users.id` throughout the function body. A bare `WHERE id = auth.uid()` becomes ambiguous — Postgres raises "column reference 'id' is ambiguous." Always qualify: `WHERE users.id = auth.uid()`. Bug history: caught when the `/admin` page failed to load workspaces; fixed in commit `53e1543`.

3. **Authorization parameters: prefer `current_user_workspace_id()` over caller-supplied workspace_id.** A function that takes `p_workspace_id uuid` as a parameter and uses it for authorization checks is tamper-prone — a caller can pass any workspace's ID. Internal use of `current_user_workspace_id()` (SECURITY DEFINER helper that maps `auth.uid()` to the user's workspace) is tamper-resistant: the caller can't influence which workspace the function operates on. Reserve workspace_id parameters for cross-workspace operations (admin functions like `list_accounts_for_workspace(p_workspace_slug)`) and gate THOSE on `is_super_user`.

4. **Prefer SQL functions over PL/pgSQL when no procedural logic is needed.** PL/pgSQL has subtle scoping rules (the OUT parameter shadowing in rule 2 is a PL/pgSQL-specific behavior). A pure-SQL function avoids that whole class of bug. Use PL/pgSQL only when you need conditionals, loops, or exception handling.

5. **`GRANT EXECUTE` to `authenticated` and `REVOKE EXECUTE` from `anon` for any super-user-gated function.** Belt-and-suspenders: the in-body gate is the primary defense; the GRANT/REVOKE is the secondary. Without the REVOKE, an anonymous caller could *attempt* the function (the gate would catch them, but only if the gate is correct — see rule 1).

The shape of these rules is mostly defensive — they prevent classes of bug that LOOK correct on first read but fail in edge cases. Code review on a SECURITY DEFINER function should walk through each rule explicitly.

### LLM-output phase discipline

When an ADR phase produces or modifies LLM output (narrative generation, audit harness, prompt edits, synthetic-data generators that feed either path), apply these rules. They protect against two distinct failure modes the 2026-04-25 → 2026-05-09 ADR-020 → ADR-021 → Scenario B arc surfaced: (a) a substantive design failure (prompt produces hallucinations at production density), and (b) a meta-failure (the audit harness designed to detect that was itself broken in a way that masked the actual rate, driving a design pivot on contaminated evidence).

1. **Audit-pass rate is the exit criterion**, not implementation completion. A phase is not done until an audit run on a representative slice of the new outputs hits the project's pass threshold (currently ≥10/12 for the lattice-build corpus, scaled equivalently for other workspaces). Structural acceptance criteria (smoke test passes, pyright clean, narrative count matches) are necessary but not sufficient.

2. **Prompt-touching commits must reference a recent audit run.** Any commit that modifies `config/prompts/narrative.v1.md`, `scripts/prompts/audit-narratives.md`, or downstream prompt-rendering code must include in its commit body a reference to an audit run (date, pass count, hard-gate failures) that exercises the change. A commit message that says "tightened coverage rule" without an audit datapoint is incomplete.

3. **Signal density is part of the prompt's input, not an environmental variable.** A prompt that works at one corpus density does not generalize to a different density. Before changing the corpus density a prompt operates over (per-week vs cumulative window, slice size, time horizon), run the audit harness against the new density first; treat the prompt as untested at the new density until that run passes.

4. **Cheapest-validation-first rule (LLM-output application).** Applies the universal cheapest-invalidation principle from `architect.md` to LLM-output phases. The cheapest invalidation test for an LLM-output assumption is typically a single audit run on a representative slice at the assumed corpus density. This test must run before downstream phases ship; the architect ADR must name it explicitly and the phase cannot close without the result.

5. **Validate the validator (LLM-output application).** Applies the universal validate-the-validator principle from `architect.md` to LLM-output phases. Project-specific trigger: any audit run producing ≤25% pass rate on ≥10 narratives requires a sanity check on a known-good baseline narrative (from `fixtures/narrative-baselines/`) before the result drives a decision. Cost ~$0.005. A run that fails on a known-good baseline confirms the harness is in degraded mode; investigate the harness invocation before acting on the mass-failure result.

See [.private/postmortem/adr-020-to-scenario-b-2026-05-09-corrected.md](.private/postmortem/adr-020-to-scenario-b-2026-05-09-corrected.md) for the canonical incident report (supersedes the earlier `2026-05-09.md` which is contaminated by the same harness bug it was analyzing).

## Common Patterns

**Domain models** (`src/domain/`): pure Python, no I/O. Immutable entities use `@dataclass(frozen=True)`; mutable ones (Account, Contact, Signal, NarrativeRegenJob) use plain `@dataclass`. All enum-like fields use `StrEnum` — values must match DB CHECK constraints exactly. All UUIDs are `uuid.UUID`; all timestamps are tz-aware `datetime`.

**Config** (`src/config/`): `load_config(workspace_slug)` deep-merges workspace overrides on top of `config/defaults.json`. `get_inbound_domain()` reads `INBOUND_DOMAIN` env var, falls back to `defaults.json`. Both are safe to call repeatedly — defaults are cached with `@functools.cache`.

**Signal sources** (`src/signals/`): `SignalSource` ABC with `async fetch(workspace_id, since) -> list[RawInboundEvent]`. Phase 1/2 use `JsonFixtureSource`; `since` must be tz-aware.

**DB layer** (`src/db/`): one module per table. Each exports typed functions (`upsert_*`, `get_*`, `insert_*`). All writes explicitly pass `workspace_id` as a column value (defense-in-depth). Row dicts ↔ domain objects via private `_from_row` / `_to_dict` helpers in each module. Never import `src.db` from `src.domain` — domain models have no I/O dependency.

**Pipeline** (`src/pipeline/`): `router.py` is a pure function (no DB calls, fully unit-testable). `normalizer.py` parses payload + upserts contacts + inserts signal. `run.py` orchestrates the full per-event flow and mutates the `accounts` list in-place as candidates are auto-discovered. `scheduler.py` calls `enqueue_regen_job` after routing. `confidence.py` is a pure function — engagement health is **deterministic** (signal count + window + contact diversity, scaled by `account.frequency_multiplier`), not decided by the LLM; returns `HealthResult` with `score: int` (1-100) and `tier_name: str`. `generator.py` calls Claude with prompt caching; the system block is static (output format + guardrails), the user block is per-account (context + signals); the LLM returns `sentiment: int` (1-100) in its JSON output alongside the narrative. Prompt content is A/B-gated: `_resolve_prompt_variant(account_id)` evaluates the `narrative-prompt-variant` PostHog feature flag (sticky per account_id — an account always sees the same variant across regenerations — defaults to `v1` if the flag is undefined or PostHog is disabled), and `_load_template_for_variant()` loads `config/prompts/narrative.v1.md` or `.v2.md` accordingly, falling back to v1 if the variant-specific file is missing. The resolved variant is tagged as a `prompt_variant` attribute on the OTel span underlying the `$ai_generation` event, so PostHog LLM Analytics can slice by variant. The `narrative-prompt-variant` flag does not yet exist in PostHog as of 2026-05-21, so resolution currently returns `v1` for every account until the flag is created. See ADR-023 (`.private/architect/adr-023-prompt-variant-flag-gating.md`) for the gating mechanism's design rationale and the activation preconditions (v2 must clear an audit-pass run at corpus density before the flag is created). `health.py` is a pure function (`compute_overall_health`) — weighted average of enabled dimension scores, returns `int | None`; None if no scores or sum-of-weights is 0. Post-narrative scoring (`_score_and_snapshot` in `generator.py`) is fire-and-log: never fail narrative generation because a scoring step failed.

**Health dimensions** (`src/db/dimension_configs.py`, `dimension_scores.py`, `health_snapshots.py`): `health_dimension_configs` is a mutable config table (has `updated_at` trigger), seeded per workspace by `process-fixtures`. `account_dimension_scores` and `account_health_snapshots` are append-only with supersede pattern (like `narratives`) — no `updated_at`, only `superseded_at`. `accounts.overall_health_score` is a denormalized cache of the latest snapshot's weighted average; updated synchronously after each scoring event.

**Migrations**: baseline file `supabase/migrations/20260423_000001_initial_schema.sql`; subsequent changes are new numbered files (incremental, currently through `000030_*.sql`). PostgREST grants for new tables: `GRANT ALL ON <table> TO authenticated, service_role;` — do NOT grant table-level access to `anon`; unauthenticated callers get only `GRANT USAGE ON SCHEMA public TO anon`. RLS uses `current_user_workspace_id()` (SECURITY DEFINER) — never inline `SELECT workspace_id FROM users WHERE id = auth.uid()` in a policy. All workspace-isolation RLS policies must include both `USING` and `WITH CHECK`: `USING (workspace_id = current_user_workspace_id()) WITH CHECK (workspace_id = current_user_workspace_id())`.

**Timestamp convention**: mutable tables use `created_at` (row creation) + `updated_at` (trigger-maintained via `set_updated_at()`). Append-only tables drop the generic `created_at` and use a column named after the event the row represents — because each append-only row IS the event, the column name should describe what happened rather than just "row inserted." Examples: `generated_at` (narratives — LLM produced output), `scored_at` (account_dimension_scores — dimension was scored), `computed_at` (account_health_snapshots — aggregate rolled up), `received_at` (raw_inbound_events — webhook arrived), `occurred_at` (audit_events — event happened), `audited_at` (narrative_audits + narrative_audit_runs — auditor evaluated). The value is `NOT NULL DEFAULT now()` like `created_at` would be; only the name changes. Append-only tables have no `updated_at` (no edits) and no `deleted_at` (supersede via `superseded_at` instead). The `signals` table is the edge case: it's mutable (so it carries `created_at` + `updated_at`) AND has a separate `occurred_at` for the event time the email was sent. Two timestamps, two jobs, distinct names.

**Soft delete**: every mutable table has `deleted_at timestamptz NULL`, including config tables. Do not carve out exceptions for "low-churn" or "config" tables — the FK integrity argument alone justifies it (hard-deleting a config row FKed by historical data forces destructive cascade or orphaned references), and SOC 2 Type II / GDPR two-stage erasure patterns depend on uniform coverage. Append-only tables (`narratives`, `audit_events`, `raw_inbound_events`, `account_dimension_scores`, `account_health_snapshots`) are excluded — they use `superseded_at` instead. See ADR-005 §Decision 8.

**Tests**: `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed. Fixture paths use `Path("fixtures/...")` relative to repo root (pytest runs from root). Count-based assertions (`assert len(events) == 71`) are intentional regression guards. DB-dependent code is tested by patching at the call site (`patch("src.pipeline.normalizer.upsert_contact", ...)`), not by mocking the client object.

**Frontend Supabase clients** (`frontend/src/lib/supabase/`): `client.ts` creates a browser client (use in `'use client'` components); `server.ts` creates a server client (use in Server Components and Route Handlers). Middleware (`src/middleware.ts`) creates its own inline client to refresh sessions. Never import `server.ts` in Client Components or `client.ts` in Server Components.

**Frontend auth**: Three sign-in modes on `/login`: Google OAuth (`supabase.auth.signInWithOAuth({ provider: 'google' })`), email+password (`supabase.auth.signInWithPassword`), and magic-link OTP (`supabase.auth.signInWithOtp`). Middleware guards all routes except `/login` and `/auth/*`. The auth callback route (`/auth/callback`) exchanges the code for a session and redirects to `/accounts`. Users must have a row in the `users` table with the correct `workspace_id` for RLS to return data — the `current_user_workspace_id()` function looks this up.

**Frontend testing**: Vitest + jsdom. Mock `@/lib/supabase/client` at the module level (`vi.mock('@/lib/supabase/client', ...)`); mock `next/navigation` for `useRouter`. Run from `frontend/` with `npm test`.

**Frontend mutation pattern**: All frontend writes go through SECURITY DEFINER RPCs (`supabase.rpc('<function_name>', {...})`). Direct table mutations via PostgREST (`supabase.from('<table>').insert/update/delete(...)`) are not permitted for `authenticated`-role callers — migration 000027 (ADR-019) revokes INSERT, UPDATE, and DELETE from `authenticated` on all public tables. Reads continue to use `supabase.from('<table>').select(...)`. Business logic, workspace-isolation checks, and any audit logging for a mutation go into the RPC body, not scattered across call sites. Any new mutable table must include: `GRANT SELECT ON <table> TO authenticated, service_role;` (no INSERT/UPDATE/DELETE to `authenticated`) and a SECURITY DEFINER RPC for any frontend mutation on that table. The `authenticated` role's surface is SELECT-only; tests mock `supabase.rpc(...)` not `supabase.from(...).update(...)`.

**Outreach draft greeting sync** (`frontend/src/components/OutreachTab.tsx`): the greeting name tracks the selected recipient via a client-side anchored swap (the `^Hi <name>,` greeting line only, regex-escaped via `escapeRegExp`), not a server re-render — edit-preserving by construction, since a user-rewritten greeting makes the swap a no-op. `lastAppliedNameContactIdRef` tracks which contact's name is *actually in the text*, independent of the dropdown's live `contactId` — required because "No recipient" (`contactId = null`) does not clear the persisted text (`update_outreach_draft`'s `p_contact_id` can't clear to NULL — ADR-019 D8), so the ref must survive the null hop for the next real recipient change to find the right name to replace. **Two invariants a future editor must preserve**: (1) derive the "old name" from the ref, never from `contactId` state directly; (2) EVERY handler that commits new subject/body text must also set `lastAppliedNameContactIdRef.current = contactId` — including `handleTemplateSelect`, so the ref stays truthful to the text even when a template is picked during a null hop (omitting this reintroduces the exact greeting-desync bug the ref exists to prevent). `handleContactChange` swaps the body only (subjects never carry `[Contact Name]` in any template). Known residual (low-severity, tracked): a "No recipient → template-select → reload-before-picking-a-real-recipient" sequence can desync the ref from the persisted placeholder text across a page reload; it degrades gracefully (no-op swap, no crash, Send stays gated). Full design + review trail in `.private/architect/outreach-greeting-sync-spec-2026-07-08.md`.

**HTTP server** (`src/server/`): FastAPI app factory in `src/server/app.py`; route handlers in `src/server/routes/`. All imports are lazy (inside `_serve()` in `src/worker.py`), matching the existing CLI pattern. Start locally: `uv run python -m src.worker serve [--port 8080]`. Health endpoint: `GET /health`. OpenAPI docs disabled by default; enable with `FASTAPI_DEBUG=true`. CORS middleware is opt-in via `CORS_ORIGINS` env var — required for the Next.js frontend to call the worker from a browser. See ADR-006 for full design rationale.

**Narrative scheduler** (`src/server/routes/scheduler.py`): `POST /run-narratives` drains the `narrative_regen_jobs` queue for all active workspaces. Authenticated via `Authorization: Bearer <SCHEDULER_SECRET>` header (separate env var from `WEBHOOK_SECRET`). Triggered by Cloud Scheduler every 15 minutes. `recover_stale_jobs()` runs once before the workspace loop, not per-workspace. See ADR-007.

**Webhook security**: `WEBHOOK_SECRET` env var required. Checked via `hmac.compare_digest` against the `?token=<secret>` query parameter on `POST /inbound`. SendGrid Inbound Parse does not support custom request headers, so a query parameter is the only practical option; the token appears in Cloud Run request logs (IAM-access-controlled — accepted tradeoff, documented in `.private/security-reviewer/findings-2026-04-24.md`). If `WEBHOOK_SECRET` is unset, the handler returns 500 (fail closed). Return HTTP 200 (not 4xx) for permanent routing failures (unknown workspace, unroutable envelope, malformed payload) — returning 4xx causes SendGrid to retry indefinitely. Example: `curl -X POST "https://host/inbound?token=$WEBHOOK_SECRET" -F envelope=...`

**`.mcp.json` project ref**: `.mcp.json` ships a hardcoded Supabase `project_ref`. This is a permanent identifier that forms the base of all REST/Auth API URLs; it is tracked in git and present in every clone. Accepted exposure — the ref alone grants no data access without a key, and `.mcp.json` is a developer tooling file only. If the repo is ever made public, move to a `SUPABASE_PROJECT_REF` env var. See `.private/security-reviewer/findings-2026-04-24.md`.

**Inbound parse module** (`src/signals/shared_inbox.py`): pure functions, no I/O, no DB. `build_raw_payload(form, inbound_domain)` converts SendGrid multipart form fields to the `InboundPayload`-compatible JSON that `normalizer.py` reads from `RawInboundEvent.raw_payload`. `extract_workspace_slug(envelope_json, inbound_domain)` → `(workspace_slug, account_slug | None)`. All parsing functions are independently testable.

**Product telemetry event** (ADR-012, [`src/server/routes/event.py`](src/server/routes/event.py), [`src/server/routes/event_js.py`](src/server/routes/event_js.py), [`src/pipeline/product_event.py`](src/pipeline/product_event.py)): `POST /event` (renamed from `/ingest` on 2026-05-08 for privacy-aware naming) accepts native + Segment payloads (single or batch). Auth via `Authorization: Bearer <key>` against the `api_keys` table; the API key scope name remains `"ingest"` as an internal auth identifier — endpoint and scope intentionally don't share names. Ingest-scope keys carry the `pk_live_` prefix, all other scopes use `sk_live_`. `verify_api_key` does a SHA-256 hash lookup with scope and expiry checks; `last_used_at` is fire-and-log. Rate limiting via in-memory sliding window per Cloud Run instance ([`src/server/rate_limit.py`](src/server/rate_limit.py)), keyed by `key_prefix`; tunable via `config/defaults.json` `api.{ingest_,}rate_limit_per_minute`. Body cap 256KB, batch cap 500 events, partial-success contract `{accepted, rejected, signal_ids, duplicate_ids, errors}`. `normalize_product_event` performs 3-way contact routing (known email → `api_key_identity`; new email → `auto_discovery`; missing email → `unmatched`) and bypasses the email-cascade router. Embeddable browser script served at `GET /event.js` (renamed from `/tracker.js` on 2026-05-08 — same privacy-aware reasoning as the endpoint rename; `tracker` is on common ad-blocker filter lists). Built from [`src/server/static/event.js`](src/server/static/event.js) via `npm run build:event-js` at repo root.

**Synthetic data generator** (ADR-015, [`src/synthetic/`](src/synthetic/)): four-layer architecture — YAML scenario authoring → per-modality generators → conversion+emission → orchestrator. Scenarios live in [`fixtures/synthetic-scenarios/`](fixtures/synthetic-scenarios/) and are validated by Pydantic (`src/synthetic/scenario.py`) with `extra="forbid"` so YAML typos surface as ValidationError at load time. Generators (`src/synthetic/generators/email.py`, `product.py`) are pure functions: seeded `random.Random` threaded through, no `datetime.now()`, deterministic `uuid5` IDs derived from `(scenario_name, signal_index)`. The orchestrator (`src/synthetic/orchestrator.py`) reads scenarios, dispatches to generators by `source_type`, and routes results through the production pipeline — `RawInboundEvent` flows through `process_event()`, `ProductEvent` flows through `normalize_product_event()` directly. **Never bypass production normalisation**; the pipeline's correctness is what's under test. `AxesSpec` carries 8 structural axes (cadence, register, threading, sentiment, etc.) plus a topical `concern_topic` axis (Rev 1) with 8 values driving template-family selection in the email generator. Synthesise to disk via `uv run python -m src.worker synthesise-fixtures --scenario <path>.yaml`; output is a workspace-shaped directory under `fixtures/synthetic/<scenario>/` with a deterministic `manifest.json` (byte-identical re-runs) and a wall-clock `last_run.json`.

**Narrative audit harness** (ADR-016, [`scripts/audit_narratives.py`](scripts/audit_narratives.py), [`scripts/prompts/audit-narratives.md`](scripts/prompts/audit-narratives.md)): cross-vendor evaluator that grades production-generated narratives on 5 criteria (faithfulness, coverage, calibration, hallucination, tone-fit). Auditor is OpenAI `gpt-5-mini-2025-08-07` (pinned snapshot — cross-vendor for training-priors independence vs same-vendor Claude Haiku). Two append-only tables: `narrative_audits` (5 criterion rows per audit invocation) + `narrative_audit_runs` (one aggregate row per `(narrative_id, audit_run_id)` with `overall_passed`, `hard_gate_failures`, `warning_failures`, `score_summary` JSONB, summed cost). Both rows committed atomically via compensating delete on aggregate-row failure. Triggers: per-PR (writes with `audit_source='ci'`, gates merges) + nightly cron (`'nightly'`, trend tracking) + manual CLI (`'manual'`). Gating: any single hard-gate failure blocks the PR. Cost ~$0.055/run for the 11-narrative corpus at default settings; `AUDIT_MAX_COST_USD` env var caps it. CLI requires explicit `--write-db` OR `--dry-run` (no silent fall-through). The CI workflow in [`.github/workflows/audit-narratives.yml`](.github/workflows/audit-narratives.yml) uses `environment: production` for maintainer-approval-gated secret injection.

**Property tests + distribution validation** (planning report §4.3 + §4.4, [`tests/test_invariants.py`](tests/test_invariants.py), [`tests/synthetic/test_dimension_distribution.py`](tests/synthetic/test_dimension_distribution.py)): three Hypothesis `@given` invariants (overall_health weighted-average, routing_confidence ∈ [0,1] across all router outcomes via two `@given` variants, uuid5 stability) + a distribution suite asserting per-scenario at-risk/healthy bands across the 6 named corpus scenarios. The distribution suite passes a per-scenario `now` to [`determine_account_health(signals, config, frequency_multiplier, now=now)`](src/pipeline/confidence.py) so the synthetic signal timeline (anchored at a fixed epoch in YAML) aligns with the engagement-window cascade without mutating signal timestamps; `_SCENARIO_RECENCY_DAYS` encodes the scenario's design-intent gap from the latest signal to "now".

**Snapshot baselines** (Phase 4c, planning report §4.5, [`fixtures/narrative-baselines/`](fixtures/narrative-baselines/)): one JSON per active narrative, capturing deterministic fields (engagement, engagement_rationale, overall_health_score) for byte-equal regression catch and LLM-produced fields (sentiment, narrative text) for human diff review during code review. [`scripts/capture_narrative_baselines.py`](scripts/capture_narrative_baselines.py) refuses to capture if any active narrative does not have a most-recent `audit_overall_passed=true` — baselines are only as good as the audit they were blessed by. [`scripts/check_narrative_baselines.py`](scripts/check_narrative_baselines.py) is the DB-coupled drift detector for manual pre-merge use; LLM-produced fields intentionally not compared (audit harness covers their semantics). [`tests/test_narrative_baselines.py`](tests/test_narrative_baselines.py) runs the structural + audit-clean invariant checks DB-free.

**LLM Observability** ([`src/observability/llm.py`](src/observability/llm.py)): OTel-based instrumentation that auto-captures `$ai_generation` events in PostHog for both the Claude narrative generation path and the GPT-5-mini audit harness. Uses `posthog[otel]` extras + `opentelemetry-instrumentation-anthropic` + `opentelemetry-instrumentation-openai-v2`. Call `setup_llm_observability()` once before constructing any LLM client; it is idempotent and self-suppresses in pytest, when `POSTHOG_API_KEY` is unset, and when `POSTHOG_LLM_OBSERVABILITY_ENABLED=false`. Entry points wired: `generate-narratives` CLI subcommand (`src/worker.py`), `_serve()` (`src/worker.py`), and `scripts/audit_narratives.py`. Configurable via `POSTHOG_LLM_OBSERVABILITY_ENABLED` (default: true when key is set) and `POSTHOG_LLM_CAPTURE_CONTENT` (default: true; maps to `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` — set false before deploying against real customer data). Architectural boundary: this module covers LLM call-level spans (cost, latency, tokens); `src/analytics.py` covers business-level product events (Narrative Generated, Signal Ingested). Both flow to the same PostHog project. PostHog LLM Analytics distinguishes `$ai_generation` (any LLM call, auto-captured by OTel as above) from `$ai_evaluation` (a verdict on an AI event's output, which requires explicit capture). `src/analytics.py`'s `track_ai_evaluation()` emits one `$ai_evaluation` event per audit criterion from `scripts/audit_narratives.py` (5 per narrative audit — faithfulness, coverage, calibration, hallucination, tone-fit), using PostHog's `$ai_metric_name`/`$ai_metric_value` shape plus audit-trail properties (`audit_run_id`, `narrative_id`, `audit_source`) and the OTel trace ID of the underlying OpenAI call, so the evaluations correlate with their `$ai_generation` in the PostHog trace view. Fire-and-log, same as the rest of `src/analytics.py`. **Both are application telemetry — distinct from the product domain event model** (`signals`, `audit_events`, `narratives`, `narrative_audits` tables in Postgres) which models customer-side artifacts (subject, body, direction, occurred_at, source_type) rather than runtime operations. OTel events trace *how* the platform processed a domain event; domain events record *what* the customer did or what the platform produced. Different shapes, different storage, different consumers — don't conflate.

## Private docs (`.private/`)

Not committed. Contains working documents produced by agents across sessions.

- [`.private/architect/`](.private/architect/) — ADRs (001–016) and phase handoff briefs (Phase 1–6, health redesign, health framework, outreach templates, identity model, product-telemetry-ingest, synthetic data generator contract, audit harness, Phase 2b/2c handoffs, Phase 3 design review)
- [`.private/code-reviewer/`](.private/code-reviewer/) — Phase-by-phase code review reports (Phase 1–4, ADR-005, Phase 6, renewal intent, sentiment dimension, outreach templates, Phase 2b/2c/3/4)
- [`.private/security-reviewer/`](.private/security-reviewer/) — Security findings and coder prompts (2026-04-24, 2026-04-25, Phase 3 audit harness 2026-05-05)
- [`.private/coder/`](.private/coder/) — Coder session notes (implementation summaries)
- [`.private/uat/`](.private/uat/) — UAT findings: open issues requiring architect or product decision
- [`.private/refactorer/`](.private/refactorer/) — Refactor findings and change log (2026-04-25)
- [`.private/product-manager/`](.private/product-manager/) — V1 spec, PM briefs (outreach templates, identity model, analytics tracking plan, LLM routing, future signal sources, product telemetry ingest)
- [`.private/market-researcher.md`](.private/market-researcher.md) — Market research
- [`.private/terminology.md`](.private/terminology.md) — Domain terminology reference
- [`.private/pilots/`](.private/pilots/) — Pilot account intelligence (e.g. Elicit)
- [`.private/PROGRESS.md`](.private/PROGRESS.md) — Progress tracker. **Moved from repo root to `.private/` on 2026-05-12** as part of pre-push cleanup (verbose working state, not public-facing surface). The global `~/.claude/CLAUDE.md` directive to "ensure PROGRESS.md exists" should resolve to this path on this project — do NOT create a new PROGRESS.md at repo root.

When making design decisions or picking up a new phase, check `.private/architect/` for the relevant ADR or handoff brief first.

## Deployment

TS frontend: Vercel — production at `<your-vercel-url>`. Python worker: GCP Cloud Run (`<your-gcp-project-id>`, `us-central1`). Service URL: `<your-cloud-run-url>`. Cloud Scheduler triggers `POST /run-narratives` every 15 minutes (UTC).

Operational specifics (deployment URLs, GCP/Vercel/PostHog identifiers, etc.) live in `.claude/internal.md` — gitignored; ask the maintainer for the current values.

## Open Questions

- [x] **Domain name** — split into two subdomains for inbound vs worker API; concrete values gitignored in `.claude/internal.md`. DNS + SendGrid Domain Auth + SendGrid Inbound Parse + Cloud Run custom domain mapping + scheduler retarget all complete (2026-05-21). Legacy inbound host left configured but inert (single-domain worker; legacy mail drops as `permanent_failure`). Public-repo files use `signal.example.com` as the RFC-2606 test placeholder.
- [x] **Cloud provider for Python worker** — GCP Cloud Run (ADR-006). min-instances=0, 512Mi, port 8080. Cloud Scheduler running `*/15 * * * *` UTC.

## Claude Code workflow

This project uses the `claude-harness` template. See:
- **Agents** (global, at `~/.claude/agents/`): market-researcher, product-manager, designer, architect, coder, tester, code-reviewer, security-reviewer, debugger, refactorer, repo-hygiene-auditor, uat, meta-agent. (`documentation` agent exists in the harness template; not yet propagated to `~/.claude/agents/` — `cp claude-harness/agents/global/documentation.md ~/.claude/agents/` to enable.)
- **Skills** (project-local, in `.claude/skills/`): update-progress, session-start, prepare-design-context, implement-design-handoff, new-feature, review-pr, tdd-cycle, setup-llm-client, design-system
- **Hooks**: UserPromptSubmit (injects date + branch context), Stop (reminds to update PROGRESS.md if uncommitted changes exist)

At the start of each session, run `/session-start` to load context. At the end, `/update-progress` to keep [`.private/PROGRESS.md`](.private/PROGRESS.md) current (path moved 2026-05-12 — see Private docs section above).

### Sub-agent delegation discipline

**Default to delegating** when a task matches a sub-agent's description. Sub-agents exist to (a) parallelize independent work, (b) protect the main context window from large reads, and (c) bring expertise the main loop doesn't have. Skipping them costs all three benefits silently.

**Always narrate the decision.** Before any non-trivial action, state which agent (if any) and why. Considered-and-skipped is also narrated:
- *"Using `debugger` for root-cause investigation; `architect` in parallel for the ADR-005 design angle."*
- *"Considered `tester` but the change is comment-only — no behavior to regression-test."*

**Triggers (don't skip these):**

| Signal | Agent |
|---|---|
| User asks "how do I test this" / "walk me through UAT" / "what should I verify?" | `uat` |
| Just wrote production code that changes runtime behavior (any non-trivial change) | `tester` for at least one regression test |
| User reports "it's broken" / "why is this happening" / a real bug surfaces | `debugger` |
| Weighing tradeoffs, picking between approaches, work touches an ADR | `architect` |
| Pre-merge of anything non-trivial | `code-reviewer` |
| Touching auth / credentials / user input / external service integration | `security-reviewer` |
| Open-ended search across many files (>3 queries, unfamiliar terrain) | `Explore` or `general-purpose` |
| Refactor / cleanup / "this is messy" | `refactorer` |
| Visual / UX / design-system work | `designer` |
| "Update the docs" / "are the docs current?" / major feature shipped without README/architecture.md reflecting it / ADR statuses outdated / findings docs reference resolved issues | `documentation` |

**Direct execution is acceptable when:** single tool call (one Read, one grep, one curl), runbook commands you've already designed, or an edit you literally just designed in the prior turn. If the work has multiple steps, requires judgment, or requires reading more than one file, the bar shifts toward delegation.

**Parallelize independent agents.** When two agents could investigate orthogonal angles (e.g., `debugger` on code path + `architect` on design intent), spawn them in a single message with multiple Agent tool calls.

**Brief like a cold colleague.** Self-contained prompt with goal, what's been ruled out, file pointers, and the expected report format. Never just "based on context, do X" — that pushes synthesis onto the agent.

**Trust but verify.** An agent's summary describes intent, not necessarily what landed. After a coder/edit-running agent finishes, check the actual diff before reporting "done".

### Sub-agent emoji catalog

When narrating an agent invocation in conversation responses, PROGRESS.md "Agents used / considered" lines, or commit-message references to agents, prefix the agent name with its assigned emoji. The combination of color (rendered by Claude Code's UI) + emoji prefix (in human-readable narration) gives multi-modal signaling in fast-moving CLI windows where multiple agents may be referenced in quick succession.

| Agent | Emoji | Agent | Emoji |
|---|---|---|---|
| `architect` | 📐 | `meta-agent` | 🧬 |
| `code-reviewer` | 🔍 | `product-manager` | 🎯 |
| `coder` | 💻 | `refactorer` | ♻️ |
| `debugger` | 🐛 | `repo-hygiene-auditor` | 🧼 |
| `designer` | 🎨 | `security-reviewer` | 🛡️ |
| `documentation` | 📝 | `tester` | 🧪 |
| `market-researcher` | 🔭 | `uat` | 🎭 |

**Scope (strict):** emoji prefixes are used **only** when narrating sub-agent invocations or listing agents in PROGRESS.md "Agents used" lines. The no-emoji rule from the writing style guide continues to apply everywhere else — prose, headings, commit message bodies, code, comments, doc text. This is a narrow carve-out for multi-modal signaling, not a general license.

Examples (correct):
- *"Spawning 🐛 `debugger` for root-cause investigation; 📐 `architect` in parallel for the ADR-005 design angle."*
- *"Considered 🧪 `tester` but the change is comment-only — no behavior to regression-test."*

### Permission mode

[.claude/settings.json](.claude/settings.json) sets `defaultMode: acceptEdits` and wires the project's UserPromptSubmit + Stop hooks. The destructive-operation deny list (`.env*` writes, `sudo`, force-push, `git reset --hard`, `git branch -D`) and the curated allow list (read-only Bash + MCP tools) live in `~/.claude/settings.json` — project settings deliberately don't duplicate them, since drift between layers is harder to reason about than a single source of truth. Press `Shift+Tab` in the CLI to cycle modes (`default` → `acceptEdits` → `plan`; `auto` appears if your account qualifies). Drop to `default` for per-action prompts, or `auto` on Opus 4.7 for classifier-gated independence.

`permissions.additionalDirectories` is configured in `.claude/settings.local.json` (gitignored) rather than in the committed `settings.json`. Add your local cross-project paths there. The harness pattern: include `~/Projects/claude-harness` so the auto-allow rules for `grep`/`ls`/`cat`/read-only `git` extend into the harness repo without prompting — supports cross-repo backporting without per-read approval prompts.
