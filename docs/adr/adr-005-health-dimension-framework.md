# ADR-005: Health Dimension Framework

**Date**: 2026-04-24
**Status**: Accepted

**Builds on**: [ADR-004](adr-004-health-scoring-redesign.md)

## Context

ADR-004 established a two-dimension health model — engagement and sentiment — computed from email activity. The natural next question is what happens when an account's health should be informed by more than one channel: support ticket volume, product usage, a manual override from the person who owns the relationship, and whatever channel comes next. A single overall health score needs to exist for sorting and at-a-glance display, but it should be assembled from an open-ended, per-workspace-configurable set of contributing channels rather than a fixed formula baked into application code.

## Decision

Represent each contributing channel as a **dimension**: a workspace-scoped configuration row carrying a type, an enabled flag, a weight, and channel-specific settings. Each dimension writes its own score for an account, and those scores — restricted to currently-enabled dimensions — combine into a single overall health number via a weighted average, computed by a small, pure function with no I/O.

Two structural choices make this durable:

- **Dimension scores and the rolled-up snapshot are append-only, never overwritten.** A new score supersedes the old one rather than replacing it in place, which preserves a full history for trend analysis rather than only ever showing the current state.
- **A manual-override dimension (a person directly setting an account's score) is written by a single atomic database transaction** — the score, the recomputed rollup, and the account-level cache all update together or not at all. Every other dimension is populated by the application after an already-completed unit of work (e.g. after a narrative finishes generating) and is deliberately best-effort: a failure to score never blocks or fails the work that triggered it, and self-corrects the next time that work runs.

## Alternatives considered

**Encode dimension types and weights as application code or an enum, rather than configuration rows.** Rejected — it would make every weight adjustment a deploy rather than a configuration change, defeating the purpose of a pluggable framework.

**Orchestrate the manual-override write as a sequence of separate application-level calls (write the score, then the rollup, then the cache).** Rejected — that sequence has no atomicity guarantee across a network boundary; a failure partway through leaves the account in an inconsistent state. A single database-side function wrapping all of it in one transaction is a correctness guarantee, not just a convenience, and is only available because this particular write has no external API call in the middle of it (unlike, say, narrative generation, which does).

**Let a new rollup simply overwrite the previous one.** Rejected — the explicit design goal is a trend line over time. Overwriting the current snapshot on every recompute would make that impossible to reconstruct later.

## Consequences

**Positive**: adding a new health-contributing channel is an additive change — a new dimension type and its scoring logic — rather than a redesign of the existing ones. The manual-override path is atomic and safe even when other parts of the pipeline are failing intermittently. History is preserved by construction, not by a separate audit log bolted on afterward.

**Negative**: because most dimension scoring is intentionally best-effort and fires after its triggering work completes, a transient failure in the scoring step can leave an account's overall health stale until the next time that triggering work runs. This is an accepted tradeoff — the alternative (blocking the primary work on a secondary scoring step succeeding) was judged worse.
