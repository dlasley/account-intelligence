# ADR-002: Narrative Regeneration Rate Limiting

**Date**: 2026-04-22
**Status**: Accepted

## Context

Narrative generation is the most expensive step in the pipeline — it's the one that calls an LLM. Left unthrottled, it's also the step most exposed to bursty and repetitive triggers: a batch of fixture data, a forwarded email thread with several messages, or a single account generating a stream of activity during a live incident could each trigger many regenerations in quick succession, most of them redundant.

Two distinct problems needed solving, not one: a short burst of near-simultaneous triggers for the same account (should collapse into a single regeneration), and sustained high-frequency activity from one chatty account over an hour (should be capped, not throttled to zero).

## Decision

Collapse bursts with a short debounce window per account: if a regeneration is already pending, a new trigger within the window does nothing. Separately, cap sustained regeneration at once per account per a fixed cooldown window, regardless of how many triggers arrive. A manual "regenerate now" action is allowed to bypass the cooldown, but is itself rate-limited per user so it can't be used to route around the cap.

Both mechanisms are implemented as plain database state — a jobs table plus a denormalized "last generated" timestamp on the account — with no new infrastructure.

## Alternatives considered

**A shorter cooldown window (roughly half the chosen length).** Rejected on a straightforward cost/value tradeoff: a narrative is a synthesis of weeks of signal, not a live dashboard. A reader gets essentially the same value from a narrative that's a few minutes older, and halving the cooldown roughly doubles LLM spend for accounts that are actually chatty, with no corresponding improvement in what the reader sees.

**A distributed lock or external queue (e.g., a cache-backed mutual-exclusion service).** Rejected as unnecessary machinery. The debounce-and-cap logic is expressible entirely as conditional SQL against a jobs table the pipeline already needs for the async generation split. Introducing a new stateful service to do the same job trades a well-understood database pattern for an additional piece of infrastructure to operate, with no capability this system actually needs.

## Consequences

**Positive**: a predictable, bounded cost envelope even for the chattiest account in the workspace; the burst case (batch load, forwarded thread) is handled cleanly by the debounce; the entire mechanism lives in the database the system already depends on.

**Negative**: during a genuinely fast-moving incident, the narrative shown to a reader can lag the most recent signal by up to the length of the cooldown window. This is treated as an acceptable tradeoff for a product whose value proposition is synthesis over time, not real-time state — not a live dashboard.
