# ADR-004: Health Scoring Redesign — Two Dimensions, Numeric Scale

**Date**: 2026-04-23
**Status**: Accepted

## Context

The first version of account health scoring produced a single three-tier label — high, medium, or low — stored directly as constrained text. Two problems emerged quickly.

First, the label conflated two different things: how much an account was communicating, and what that communication actually said. A high-volume account escalating complaints and a quiet account sending one calm renewal email could land on the same label, or the wrong ones relative to each other — volume was doing all the work, tone none of it.

Second, the label set was enforced at the schema level. Adding a tier, renaming one, or changing the boundary between two meant a database migration. A display concern — what to call a given range of health — had been given the cost and risk profile of a structural change.

## Decision

Split health into two independent, numeric dimensions: an **engagement** score (deterministic, derived from communication volume and recency) and a **sentiment** score (derived from communication tone). Both are stored as integers on a 1–100 scale. No label string is stored in the database at all — display labels are computed from configuration at read time, so changing what a score range is called is a config edit, not a migration.

## Alternatives considered

**Keep enforced tier labels, just rename or extend the set.** Rejected — it doesn't address the volume/tone conflation, and it keeps a pure display decision coupled to the schema, forcing every future label change through a migration.

**Cache the sentiment score as its own always-present column, independent of the numeric redesign.** Rejected for v1 — nothing outside the one place that already reads it needed a separate cached copy. Adding one speculatively, before a second consumer exists, is complexity paid for before it's earned.

**Treat a missing sentiment score as zero.** Rejected. A missing score means "not yet assessed" — an account that hasn't been scored yet, not one that scored badly. Defaulting to zero would artificially sink every newly onboarded account to the bottom of a sorted list. A missing score is instead treated as neutral wherever a composite value is needed, and omitted honestly wherever it isn't.

## Consequences

**Positive**: adjusting where a "healthy" range starts, or adding a new named tier, is now a configuration change with no migration and no deploy of new logic. The two dimensions can be reasoned about, weighted, and displayed independently, which is a truer picture of an account's state than a single blended label ever was.

**Negative**: the change required a one-time migration with an explicit backfill — mapping every existing label to a representative numeric midpoint so no historical data was lost, and dropping the old label type entirely once the migration completed.
