# Architecture Decision Records

A curated, public subset of the design decisions behind this platform. Each ADR states a decision, the alternatives weighed, and the consequences — the record of *why* the system is shaped the way it is, not just what it does.

Full internal design history (all ADRs, phase handoffs, postmortems) is maintained privately; the records surfaced here are the ones with the clearest standalone value for a reader trying to understand the engineering approach.

## Records

- **[ADR-023](adr-023-prompt-variant-flag-gating.md) — Prompt-variant gating via a sticky feature flag.** Why prompt *content* is made runtime-selectable per account behind a feature flag, and the governance rule that a variant must clear the existing quality bar before it ever reaches live traffic.
- **[ADR-010](adr-010-outreach-templates.md) — Outreach templates, replacing LLM-generated drafts.** Why a shipped LLM drafting feature was removed in favor of deterministic templates after testing surfaced fabricated content — and what replaced it.
- **[ADR-022](adr-022-vercel-ai-gateway-deferred.md) — Vercel AI Gateway, deferred.** Why a well-regarded piece of infrastructure was deliberately not adopted, with the specific conditions that would reopen the question.
- **[ADR-004](adr-004-health-scoring-redesign.md) — Health scoring redesign: two dimensions, numeric scale.** Splitting a single conflated health label into independent, config-driven engagement and sentiment scores.
- **[ADR-005](adr-005-health-dimension-framework.md) — Health dimension framework.** Generalizing account health into a pluggable, per-workspace-configurable set of weighted signal channels.
- **[ADR-013](adr-013-contact-account-linkage.md) — Contact-account linkage on ingest.** Fixing a silent data-integrity gap so contacts resolve to the right account automatically, without ever guessing on an ambiguous match.
- **[ADR-009](adr-009-sentiment-dimension.md) — Sentiment dimension.** Diagnosing and fixing a case where account health rose on unambiguously bad news, tracing it to a schema constraint rather than a logic bug.
- **[ADR-001](adr-001-inbound-mail-provider.md) — Inbound mail provider.** Vendor selection for turning email into account signal.
- **[ADR-002](adr-002-narrative-rate-limiting.md) — Narrative regeneration rate limiting.** Bounding the cost of the pipeline's most expensive step without a new piece of infrastructure.

See also [../architecture.md](../architecture.md) for the system overview and [../signal-routing.md](../signal-routing.md) for the inbound-email routing cascade.
