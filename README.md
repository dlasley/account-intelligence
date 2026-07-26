# Account Intelligence
_Zero-Config Inbound Email · Narrative Health Scoring · Cross-Vendor Audit_

An account intelligence tool for early-stage AI/SaaS teams — founders, AEs, and CSMs — who need to know what's happening with every account without spending time piecing it together.

The system reads inbound email and produces narrative account health assessments from day one, without manual configuration or data entry. It inverts the standard customer-success-tool model: signal processing and health scoring happen on first ingest, not after weeks of setup.

---

## Architecture

Two deployables, one repo:

```text
Inbound Email
  │
  ▼  (SendGrid Inbound Parse → POST /inbound)
┌────────────────────────────────────────────────────┐
│  Python Worker (GCP Cloud Run)                     │
│                                                    │
│  /inbound                                          │
│    shared_inbox.py  → parse + workspace extract    │
│    normalizer.py    → upsert contact, insert signal│
│    router.py        → 6-stage routing cascade      │
│    scheduler.py     → enqueue narrative regen job  │
│                                                    │
│  /run-narratives (Cloud Scheduler, every 15 min)   │
│    confidence.py    → engagement health score      │
│    generator.py     → Claude API, prompt caching   │
│    health.py        → weighted dimension average   │
└─────────────────────────┬──────────────────────────┘
                          │
                          ▼
               Supabase (PostgreSQL + RLS)
                          │
                          ▼
┌────────────────────────────────────────────────────┐
│  Next.js Frontend (Vercel)                         │
│                                                    │
│  Account list · Account detail · Signals timeline  │
│  Health dimensions · Outreach tab                  │
└────────────────────────────────────────────────────┘
```

The Python worker owns all signal processing, narrative generation, and health scoring. The frontend reads directly from Supabase for account and narrative data; it calls the worker only for outreach context (template recommendation + signal surfacing). No data processing happens in the browser.

### Product telemetry path

In addition to inbound email, the worker accepts product usage events via `POST /event` (ADR-012, ADR-013; renamed from `/ingest` on 2026-05-08 for privacy-aware naming). Events are authenticated with a workspace API key (`Authorization: Bearer pk_live_<16 hex>`).

```text
Product Events (POST /event, Bearer pk_live_* API key)
  │
  ▼
pipeline/product_event.py  — 3-way contact routing:
  known email      → api_key_identity  (existing contact matched)
  new email        → auto_discovery    (ADR-013: contact auto-linked to account by domain)
  missing email    → unmatched
  │
  ▼  (same downstream path as inbound email)
  scheduler.py     → enqueue narrative regen job
```

The embeddable browser script is served at `GET /event.js` (built from `src/server/static/event.js` via `npm run build:event-js`). Ingest-scope API keys (`pk_live_*`) are stored per-workspace in the `api_keys` table, not in environment variables.

See [docs/architecture.md](docs/architecture.md) for pipeline internals, health scoring design, routing cascade detail, and the outreach template system.

---

## Key Design Decisions

**No configuration required before value.** Accounts and contacts are auto-discovered from inbound email domains. The first email to the inbound address creates the account and contact; the first narrative generates without any setup step.

**Deterministic health scoring, not LLM-decided.** Email engagement health is a pure function of signal count, recency window, and contact diversity — scaled by a per-account `frequency_multiplier`. A product-usage dimension, scored the same deterministic way from telemetry events, carries equal weight alongside email engagement. The LLM narrates; it does not score. Sentiment is extracted from the narrative as an integer (1-100) and wired as a separate health dimension. A fourth dimension, CSM score, is set directly by a human via the `set_csm_score` RPC rather than computed from any signal. Overall health is a weighted average across whichever dimensions currently have an active score — a dimension with none simply drops out of the average, and the remaining weights renormalize rather than treating the missing dimension as zero.

**Vendor-neutral analytics wrapper.** Both the worker and frontend emit product-analytics and LLM-observability events through a single internal interface rather than importing the PostHog SDK directly at each call site; swapping analytics providers means rewriting the wrapper body, not touching call sites.

**No LLM in the send path.** Outreach generation was initially LLM-based; hallucination risk led to replacing it with file-based templates and signal surfacing. The frontend displays the relevant signals and lets the account team write the message. Templates use `[placeholder]` slots; the send button is blocked until all are filled.

**Fire-and-log post-narrative scoring.** Health dimension scoring runs after narrative generation as a separate step that never fails the narrative. A scoring failure is logged and skipped; the narrative is always persisted.

**Synthetic data + cross-vendor audit.** A YAML-declarative synthetic data generator (ADR-015) produces reproducible signal corpora that flow through the production pipeline without bypass. Generated narratives are graded by an OpenAI GPT-5-mini auditor (ADR-016) on five criteria — faithfulness, coverage, calibration, hallucination, tone-fit — with results persisted to `narrative_audits` + `narrative_audit_runs`. The audit gates per-PR merges (via a GitHub Actions `production` environment that injects secrets only after maintainer approval) and runs nightly to track narrative-quality drift over time.

**Webhook security tradeoff.** SendGrid Inbound Parse does not support custom request headers. The webhook secret is passed as a `?token=` query parameter, which appears in Cloud Run request logs (IAM-access-controlled). This is a documented accepted tradeoff. See [docs/architecture.md](docs/architecture.md) for the full decision.

**Structured signals extend beyond email and telemetry, unvalidated against live vendor traffic.** Plain and Pylon ticket webhooks (push) and Granola meeting notes (poll) route through the same three-way contact-linkage logic as product telemetry, sharing one normalizer. The adapters are built to each vendor's documented webhook/API shape; none has been exercised against real vendor traffic yet, so treat the parsing logic as unverified until a live account confirms it.

**Super-user browsing bypasses RLS by explicit design, not oversight.** A small `/admin` surface (three routes: workspace list, per-workspace account list, per-account detail) lets a flagged super-user (`users.is_super_user`) browse any workspace's data for support and debugging. Access is checked twice: Next.js middleware blocks `/admin/*` for non-super-users before any query runs, and each of the three SECURITY DEFINER RPCs it calls re-checks `is_super_user` inside the function body before bypassing RLS. `authenticated` stays SELECT-only everywhere else, and the admin surface itself is read-only — none of its RPCs write anything. See [docs/architecture.md](docs/architecture.md) for the route/RPC detail.

---

## Data Model

Seven entities, all workspace-scoped:

```text
organization
  └── workspace
        ├── user           (Supabase Auth identity + workspace FK)
        ├── account        (a customer company being tracked)
        │     ├── contact  (an individual at the account, matched by email domain)
        │     └── signal   (a single inbound email event)
        └── outreach_drafts
```

Health scoring adds three append-only tables per account: `account_dimension_scores`, `account_health_snapshots`, and `narratives`. Each uses a supersede pattern — a new row replaces the prior active row via `superseded_at`, preserving full history.

---

## Project Structure

```text
account-intelligence/
├── src/                          # Python worker package
│   ├── worker.py                 # CLI entrypoint: process-fixtures, generate-narratives, serve, synthesise-fixtures
│   ├── domain/                   # Pure Python dataclasses and StrEnums; no I/O
│   ├── db/                       # One module per table; typed upsert/get/insert functions
│   ├── pipeline/                 # Signal processing and narrative generation
│   │   ├── router.py             # Pure function: 6-stage routing cascade
│   │   ├── normalizer.py         # Contact upsert + signal insert
│   │   ├── _contact_factory.py   # Shared contact-creation helper used by normalizer + product_event
│   │   ├── confidence.py         # Engagement health score (deterministic)
│   │   ├── generator.py          # Claude API call + prompt caching + health scoring
│   │   ├── health.py             # Weighted dimension average (pure function)
│   │   ├── outreach.py           # Template loading, recommendation, signal panel
│   │   ├── product_event.py      # Product telemetry normalisation (3-way contact routing)
│   │   ├── product_usage_render.py # Product-usage dimension rationale text
│   │   ├── structured_signal.py  # Plain/Pylon/Granola normalisation (shared 3-way contact routing)
│   │   ├── run.py                # process_event(): production entry point for email + synthetic signals
│   │   └── scheduler.py          # Enqueue/drain narrative regen jobs
│   ├── synthetic/                # Synthetic data generator (ADR-015)
│   │   ├── orchestrator.py       # YAML scenario → seeded RNG → per-modality dispatch
│   │   ├── scenario.py           # Pydantic schema (extra="forbid")
│   │   ├── generators/           # Pure functions: email.py (5 registers × 7 topical families), product.py, meeting_note.py, ticket_plain.py, ticket_pylon.py
│   │   └── materialise.py        # Write deterministic scenario output to fixtures/synthetic/<scenario>/
│   ├── simulator/                # Trajectory backfill: replays synthesised signals through the production pipeline per historical week
│   ├── server/                   # FastAPI app (serve subcommand)
│   │   └── routes/               # /health, /inbound, /run-narratives, /run-polls, /outreach/{slug}/context, /outreach/send/{draft_id}, /event, /event.js, /signal/{kind}
│   ├── signals/                  # SignalSource ABC + JsonFixtureSource
│   ├── integrations/             # Plain/Pylon/Granola adapters + credential encryption (ADR-020)
│   ├── observability/            # LLM OTel instrumentation for PostHog LLM analytics
│   └── config/                   # Config loader (deep-merge workspace overrides on defaults)
├── scripts/
│   ├── audit_narratives.py            # Cross-vendor narrative audit harness (ADR-016, OpenAI GPT-5-mini; prompt at scripts/prompts/audit-narratives.md)
│   ├── capture_narrative_baselines.py # Phase 4c: snapshot active narratives + scores; refuses non-audit-clean
│   ├── check_narrative_baselines.py   # Phase 4c: DB-coupled drift detector vs committed baselines
│   ├── derive_quantas_labs_baseline.py # One-shot fixture-equivalence baseline derivation
│   ├── reanchor_demo_data.py          # Shifts a demo workspace's signal timestamps forward so a synthetic corpus reads as current
│   ├── simulate_history.py            # Trajectory simulator CLI (src/simulator/): backfills per-week historical narratives from a YAML spec
│   └── validate_per_week.py           # Fast targeted per-week regression check for a handful of accounts after a prompt edit
├── tests/                        # pytest tests (asyncio_mode = "auto")
│   ├── synthetic/                # Orchestrator, equivalence, audit integration, dimension distribution
│   ├── test_invariants.py        # Hypothesis property tests
│   ├── test_audit_harness.py     # Audit harness unit tests
│   └── test_narrative_baselines.py # Phase 4c: structural + audit-clean invariant checks
├── config/
│   ├── defaults.json             # Base config: health weights, model, templates path
│   ├── workspaces/               # Per-workspace config overrides (slug-keyed)
│   └── templates/outreach/       # 6 markdown templates with YAML frontmatter
├── fixtures/
│   ├── synthetic-scenarios/      # YAML scenarios driving the synthetic generator + audit corpus
│   └── narrative-baselines/      # Phase 4c committed snapshots of audit-clean narratives
├── supabase/
│   └── migrations/               # 30 numbered SQL migrations (baseline + incremental, 000001–000030)
├── frontend/                     # Next.js 15 App Router frontend
│   └── src/
│       ├── app/                  # Pages: / (redirects to /accounts), /login, /accounts, /accounts/[slug], /admin, /admin/workspaces/[slug]/accounts, /admin/workspaces/[slug]/accounts/[accountSlug]
│       ├── components/           # AccountTabs, NarrativeSection, OutreachTab, WorkspaceTable, AccountTable, etc.
│       └── lib/                  # Supabase browser/server clients, utils
├── docs/
│   └── architecture.md           # Pipeline internals, routing cascade, health design, audit harness
├── .github/workflows/
│   └── audit-narratives.yml      # Per-PR + nightly cron narrative-audit gate (ADR-016)
├── Dockerfile                    # Cloud Run container (non-root, port 8080)
└── pyproject.toml                # uv + hatch build; ruff config; pytest config; openai + hypothesis deps
```

---

## Environment Configuration

### Python Worker (Cloud Run)

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Narrative generation (Claude API) |
| `OPENAI_API_KEY` | No | Audit harness only (`scripts/audit_narratives.py`). Required to run the cross-model narrative audit; not used by the worker proper. |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service-role key for worker writes |
| `SUPABASE_ANON_KEY` | No | Not read by the worker itself; canonical value the frontend's `NEXT_PUBLIC_SUPABASE_ANON_KEY` mirrors |
| `SUPABASE_ACCESS_TOKEN` | No | Supabase MCP server only; not read by the worker at runtime |
| `SUPABASE_PROJECT_REF` | No | Supabase MCP server only, substituted into `.mcp.json` URLs; not read by the worker at runtime |
| `WEBHOOK_SECRET` | Yes | HMAC token for SendGrid inbound validation |
| `SCHEDULER_SECRET` | Yes | Bearer token for Cloud Scheduler auth on `/run-narratives` and `/run-polls` |
| `SENDGRID_API_KEY` | Yes | SendGrid Transactional API key for outreach send |
| `INTEGRATION_ENCRYPTION_KEY` | No | Encrypts Plain/Pylon/Granola credentials (ADR-020). Read lazily per-request/per-poll, not at startup — the worker boots fine without it if no structured integrations are configured; required only once one is. |
| `INBOUND_DOMAIN` | No | Inbound email domain (e.g. `signal.yourdomain.com`). Falls back to `defaults.json`. |
| `CORS_ORIGINS` | No | Comma-separated allowed origins (e.g. Vercel URL + `http://localhost:3000`). Unset = no CORS headers. |
| `FASTAPI_DEBUG` | No | Set to `true` to enable the `/docs` UI locally. |
| `AUDIT_MAX_COST_USD` | No | Cost ceiling for one audit run (default `0.50`). |
| `LOG_LEVEL` | No | Defaults to `WARNING`. Set `INFO` for pipeline visibility. |
| `POSTHOG_API_KEY` | No | PostHog project API key, shared by product analytics and LLM observability. |
| `POSTHOG_HOST` | No | Defaults to `https://us.i.posthog.com`. |
| `POSTHOG_ENABLED` | No | Product analytics on/off. Defaults `false`. |
| `POSTHOG_LLM_OBSERVABILITY_ENABLED` | No | LLM OTel instrumentation on/off. Defaults `true` when `POSTHOG_API_KEY` is set. |
| `DEPLOY_ENV` | No | Injected into OTel resource attributes. Defaults `development`; set `production` in prod. |
| `APP_ENV` | No | Product-analytics `distinct_id` prefix. Set `production` in prod. |

### Next.js Frontend (Vercel)

| Variable | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Same value as `SUPABASE_URL` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anon key (safe in client bundle; RLS controls access) |
| `NEXT_PUBLIC_WORKER_URL` | Yes | Cloud Run service URL. Use `http://localhost:8080` locally. |
| `NEXT_PUBLIC_POSTHOG_ENABLED` | No | Defaults to `false`; analytics no-op without it |
| `NEXT_PUBLIC_POSTHOG_KEY` | No | Required only when analytics are enabled |
| `NEXT_PUBLIC_POSTHOG_HOST` | No | PostHog ingestion host |

Never add `SUPABASE_SERVICE_ROLE_KEY` or `ANTHROPIC_API_KEY` to Vercel — those belong on Cloud Run only.

---

## Run It Locally, No Accounts

Everything in this section runs against nothing but this checkout — no Supabase project, no Anthropic key, no `.env` file. Skip to [Getting Started](#getting-started) below if you already have a Supabase project and an Anthropic key and want real narrative output.

### Prove the code works

```bash
uv sync
cd frontend && npm install && cd ..
uv run pytest                     # 811 passed, 5 skipped
cd frontend && npm test && cd ..  # 79 passed
```

### See the pipeline reason about real data (no database)

```bash
uv run python -m src.worker ingest-fixtures --scenario synthetic/seed-stage-saas
```

Loads one of the four committed synthetic scenarios (`fixtures/synthetic/`) and prints the routing preview for its inbound events. This never imports `src.db` — no database, no LLM call, no network.

### Run the full worker pipeline against a local database

Needs Docker and the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started) — both free, neither needs a supabase.com account. `supabase start` runs a self-contained local stack (Postgres, Auth, PostgREST, Studio) in Docker; the 30 tracked migrations apply to it exactly as they would to a hosted project.

```bash
supabase start
supabase status -o env   # prints API_URL, ANON_KEY, SERVICE_ROLE_KEY for the commands below

SUPABASE_URL=<API_URL> SUPABASE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY> \
  uv run python -m src.worker process-fixtures --scenario synthetic/seed-stage-saas

SUPABASE_URL=<API_URL> SUPABASE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY> \
  uv run python -m src.worker generate-narratives --workspace-slug lattice-build --all --fake-llm
```

`--fake-llm` (equivalently, `FAKE_LLM=true` in the environment) swaps in a canned local stub for the Anthropic client — no `ANTHROPIC_API_KEY`, no outbound call. Every narrative it writes is prefixed `[FAKE-LLM STUB]` with a fixed low sentiment, so it can't be mistaken for real model output; drop the flag and set `ANTHROPIC_API_KEY` once you want to see the actual product. Never point `scripts/audit_narratives.py` at this output — grading canned text produces a meaningless pass rate.

`supabase/seed.sql` runs automatically on `supabase start` and `supabase db reset`. It pre-creates the `lattice-build` organization and workspace with the same deterministic ID `process-fixtures` computes from the scenario's slug, so the two commands above always land on the same rows regardless of run order.

Inspect the result via Studio (URL printed by `supabase start`, default `http://127.0.0.1:54323`) or `psql`/`docker exec` against the printed `DB_URL`. Run `supabase stop` when done.

### See it in the actual frontend

The frontend reads Supabase directly, so pointing it at the local stack from the previous section shows the same data in the real UI. One manual step is required — there's no self-serve signup-to-workspace flow yet.

1. Point the frontend at the local stack (`frontend/.env.local`, or exported in your shell):

   ```bash
   NEXT_PUBLIC_SUPABASE_URL=<API_URL>          # same API_URL as above
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<ANON_KEY>    # same ANON_KEY as above — safe to expose, RLS gates data
   ```

   These are printed by `supabase status`. They're the well-known Supabase CLI local-dev defaults, identical across every default local install — not a leaked project secret.

2. Create an auth user via Studio → **Authentication → Users → Add user** (email + password), then copy its UUID.

3. Link it to the seeded workspace by running this in Studio's SQL editor (or `psql`), substituting the UUID from step 2:

   ```sql
   insert into public.users (id, workspace_id, email, display_name, role)
   values (
     '<auth-user-uuid-from-step-2>',
     'e20008c3-2f9f-5717-a076-eb101fd99bd8',  -- lattice-build, seeded by supabase/seed.sql
     'you@example.com',
     'Your Name',
     'admin'
   );
   ```

4. `cd frontend && npm run dev`, then log in at `http://localhost:3000/login` with the email/password from step 2.

---

## Getting Started

### Prerequisites

- Python 3.11+ with [uv](https://github.com/astral-sh/uv)
- Node.js 18+
- Supabase project (apply migrations via the SQL Editor)
- Anthropic API key

### Installation

```bash
uv sync
cd frontend && npm install && cd ..
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, WEBHOOK_SECRET, SCHEDULER_SECRET
```

### Apply migrations

Apply each file in `supabase/migrations/` in order via the Supabase Dashboard SQL Editor.

### Run locally

```bash
cd frontend && npm run dev            # Next.js dev server → http://localhost:3000
uv run python -m src.worker serve    # Python worker → http://localhost:8080
```

### Load fixture data

```bash
# Process the seed-stage-saas scenario (12 fictional accounts for a CI/CD observability
# startup; populates the `lattice-build` workspace per the YAML's workspace_slug).
uv run python -m src.worker process-fixtures --scenario synthetic/seed-stage-saas

# Generate narratives for every account in the resulting workspace.
uv run python -m src.worker generate-narratives --workspace-slug lattice-build --all
```

The scenario name and workspace slug differ: `seed-stage-saas` is the YAML file name (and
the conceptual scenario); `lattice-build` is the workspace slug it materialises into. See
`fixtures/synthetic-scenarios/seed-stage-saas.yaml` (`workspace_slug:` field) for the
mapping.

### Test

```bash
uv run pytest                          # Python tests (815 collected)
cd frontend && npm test                # Vitest
```

---

## Deployment

- **Python worker**: GCP Cloud Run. Deployment runbook kept internally, not included in this repo.
- **Frontend**: Vercel. Deployment runbook kept internally, not included in this repo.

Production URLs:
- Worker: `<your-cloud-run-url>`
- Frontend: `<your-vercel-url>`

---

## Development Philosophy

Built using AI-assisted development tooling while maintaining human ownership of architectural decisions, data model design, and evaluation strategy. AI accelerated implementation; system decomposition, health scoring design, and security controls were deliberate and human-directed.

The focus throughout:
- Deterministic scoring over LLM-decided health. The model narrates; it does not score.
- Templates over generation in the send path. Hallucination risk is structural, not a prompt problem.
- Fire-and-log for post-generation steps. Scoring failures never block narrative delivery.
- Workspace isolation at every layer. RLS, explicit `workspace_id` column writes, and a SECURITY DEFINER lookup function — defense-in-depth, not a single control.

---

## License

Licensed under the Business Source License 1.1. See [LICENSE](LICENSE). Converts to MIT License on 2030-05-12.
