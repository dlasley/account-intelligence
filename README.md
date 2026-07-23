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
│  /run-narratives (Cloud Scheduler, every 15 min)  │
│    confidence.py    → engagement health score      │
│    generator.py     → Claude API, prompt caching  │
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

**No configuration before value.** Accounts and contacts are auto-discovered from inbound email domains. The first email to the inbound address creates the account and contact; the first narrative generates without any setup step.

**Deterministic health scoring, not LLM-decided.** Email engagement health is a pure function of signal count, recency window, and contact diversity — scaled by a per-account `frequency_multiplier`. A product-usage dimension, scored the same deterministic way from telemetry events, carries equal weight alongside email engagement. The LLM narrates; it does not score. Sentiment is extracted from the narrative as an integer (1-100) and wired as a separate health dimension.

**Vendor-neutral analytics wrapper.** Both the worker and frontend emit product-analytics and LLM-observability events through a single internal interface rather than importing the PostHog SDK directly at each call site; swapping analytics providers means rewriting the wrapper body, not touching call sites.

**No LLM in the send path.** Outreach generation was initially LLM-based; hallucination risk led to replacing it with file-based templates and signal surfacing. The frontend displays the relevant signals and lets the account team write the message. Templates use `[placeholder]` slots; the send button is blocked until all are filled.

**Fire-and-log post-narrative scoring.** Health dimension scoring runs after narrative generation as a separate step that never fails the narrative. A scoring failure is logged and skipped; the narrative is always persisted.

**Synthetic data + cross-vendor audit.** A YAML-declarative synthetic data generator (ADR-015) produces reproducible signal corpora that flow through the production pipeline without bypass. Generated narratives are graded by an OpenAI GPT-5-mini auditor (ADR-016) on five criteria — faithfulness, coverage, calibration, hallucination, tone-fit — with results persisted to `narrative_audits` + `narrative_audit_runs`. The audit gates per-PR merges (via a GitHub Actions `production` environment that injects secrets only after maintainer approval) and runs nightly to track narrative-quality drift over time.

**Webhook security tradeoff.** SendGrid Inbound Parse does not support custom request headers. The webhook secret is passed as a `?token=` query parameter, which appears in Cloud Run request logs (IAM-access-controlled). This is a documented accepted tradeoff. See [docs/architecture.md](docs/architecture.md) for the full decision.

---

## Data Model

Six entities, all workspace-scoped:

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
│   │   ├── confidence.py         # Engagement health score (deterministic)
│   │   ├── generator.py          # Claude API call + prompt caching + health scoring
│   │   ├── health.py             # Weighted dimension average (pure function)
│   │   ├── outreach.py           # Template loading, recommendation, signal panel
│   │   ├── product_event.py      # Product telemetry normalisation (3-way contact routing)
│   │   └── scheduler.py          # Enqueue/drain narrative regen jobs
│   ├── synthetic/                # Synthetic data generator (ADR-015)
│   │   ├── orchestrator.py       # YAML scenario → seeded RNG → per-modality dispatch
│   │   ├── scenario.py           # Pydantic schema (extra="forbid")
│   │   ├── generators/           # Pure functions: email.py (5 registers × 7 topical families), product.py
│   │   └── materialise.py        # Write deterministic scenario output to fixtures/synthetic/<scenario>/
│   ├── server/                   # FastAPI app (serve subcommand)
│   │   └── routes/               # /inbound, /run-narratives, /run-polls, /outreach/{slug}/context, /outreach/send/{draft_id}, /event, /event.js, /signal/{kind}
│   ├── signals/                  # SignalSource ABC + JsonFixtureSource
│   └── config/                   # Config loader (deep-merge workspace overrides on defaults)
├── scripts/
│   ├── audit_narratives.py            # Cross-vendor narrative audit harness (ADR-016, OpenAI GPT-5-mini)
│   ├── capture_narrative_baselines.py # Phase 4c: snapshot active narratives + scores; refuses non-audit-clean
│   ├── check_narrative_baselines.py   # Phase 4c: DB-coupled drift detector vs committed baselines
│   └── derive_quantas_labs_baseline.py # One-shot fixture-equivalence baseline derivation
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
│       ├── app/                  # Pages: /login, /accounts, /accounts/[slug]
│       ├── components/           # AccountTabs, NarrativeSection, OutreachTab, etc.
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
| `WEBHOOK_SECRET` | Yes | HMAC token for SendGrid inbound validation |
| `SCHEDULER_SECRET` | Yes | Bearer token for Cloud Scheduler auth on `/run-narratives` |
| `SENDGRID_API_KEY` | Yes | SendGrid Transactional API key for outreach send |
| `INBOUND_DOMAIN` | No | Inbound email domain (e.g. `signal.yourdomain.com`). Falls back to `defaults.json`. |
| `CORS_ORIGINS` | No | Comma-separated allowed origins (e.g. Vercel URL + `http://localhost:3000`). Unset = no CORS headers. |
| `AUDIT_MAX_COST_USD` | No | Cost ceiling for one audit run (default `0.50`). |
| `LOG_LEVEL` | No | Defaults to `WARNING`. Set `INFO` for pipeline visibility. |

### Next.js Frontend (Vercel)

| Variable | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Same value as `SUPABASE_URL` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anon key (safe in client bundle; RLS controls access) |
| `NEXT_PUBLIC_WORKER_URL` | Yes | Cloud Run service URL. Use `http://localhost:8080` locally. |

Never add `SUPABASE_SERVICE_ROLE_KEY` or `ANTHROPIC_API_KEY` to Vercel — those belong on Cloud Run only.

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
uv run python -m src.worker process-fixtures --scenario seed-stage-saas

# Generate narratives for every account in the resulting workspace.
uv run python -m src.worker generate-narratives --workspace-slug lattice-build --all
```

The scenario name and workspace slug differ: `seed-stage-saas` is the YAML file name (and
the conceptual scenario); `lattice-build` is the workspace slug it materialises into. See
`fixtures/synthetic-scenarios/seed-stage-saas.yaml` (`workspace_slug:` field) for the
mapping.

### Test

```bash
uv run pytest                          # Python tests (813 passing)
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
