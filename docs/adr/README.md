# Architecture Decision Records

A curated, public subset of the design decisions behind this platform. Each ADR states a decision, the alternatives weighed, and the consequences — the record of *why* the system is shaped the way it is, not just what it does.

Full internal design history (all ADRs, phase handoffs, postmortems) is maintained privately; the records surfaced here are the ones with the clearest standalone value for a reader trying to understand the engineering approach.

## Records

_Curated set — being populated. Planned:_

- **ADR-016 — Cross-vendor narrative audit harness.** Why generated narratives are graded by a different-vendor model (training-priors independence) on five criteria, with any single hard-gate failure blocking a merge.
- **ADR-023 — Prompt-variant gating via a sticky feature flag.** Why narrative prompt *content* is A/B-tested behind a per-account-sticky PostHog flag, and the governance rule that each variant must clear the audit-pass bar at corpus density before receiving live traffic.

See also [../architecture.md](../architecture.md) for the system overview and [../signal-routing.md](../signal-routing.md) for the inbound-email routing cascade.
