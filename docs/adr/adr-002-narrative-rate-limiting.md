# ADR-002: Narrative Regeneration Rate Limiting

**Date**: 2026-04-22
**Status**: Accepted — Decision text corrected 2026-07-26 (see note at end)

## Context

Narrative generation is the most expensive step in the pipeline — it's the one that calls an LLM. Left unthrottled, it's also the step most exposed to bursty and repetitive triggers: a batch of fixture data, a forwarded email thread with several messages, or a single account generating a stream of activity during a live incident could each trigger many regenerations in quick succession, most of them redundant.

Two distinct problems needed solving, not one: a short burst of near-simultaneous triggers for the same account (should collapse into a single regeneration), and sustained high-frequency activity from one chatty account over an hour (should be capped, not throttled to zero).

## Decision

Collapse bursts with a short debounce window per account: if a regeneration is already pending, a new trigger within the window does nothing. Separately, cap sustained regeneration at once per account per a fixed cooldown window, regardless of how many triggers arrive. Both mechanisms apply identically no matter what triggered the request — a manual "regenerate now" action shares the same debounce and cooldown as an automatic trigger, rather than getting a separate path. A manual request made while either window is active isn't rejected; it's queued to run as soon as the window clears. Because the per-account cap already bounds cost regardless of who or what triggered the request, no separate per-user throttle is needed.

Both mechanisms are implemented as plain database state — a jobs table recording each request's status and completion time — with no new infrastructure.

## Alternatives considered

**A shorter cooldown window (roughly half the chosen length).** Rejected on a straightforward cost/value tradeoff: a narrative is a synthesis of weeks of signal, not a live dashboard. A reader gets essentially the same value from a narrative that's a few minutes older, and halving the cooldown roughly doubles LLM spend for accounts that are actually chatty, with no corresponding improvement in what the reader sees.

**A distributed lock or external queue (e.g., a cache-backed mutual-exclusion service).** Rejected as unnecessary machinery. The debounce-and-cap logic is expressible entirely as conditional SQL against a jobs table the pipeline already needs for the async generation split. Introducing a new stateful service to do the same job trades a well-understood database pattern for an additional piece of infrastructure to operate, with no capability this system actually needs.

## Consequences

**Positive**: a predictable, bounded cost envelope even for the chattiest account in the workspace; the burst case (batch load, forwarded thread) is handled cleanly by the debounce; the entire mechanism lives in the database the system already depends on.

**Negative**: during a genuinely fast-moving incident, the narrative shown to a reader can lag the most recent signal by up to the length of the cooldown window, and a manual regenerate can't shortcut that lag either. This is treated as an acceptable tradeoff for a product whose value proposition is synthesis over time, not real-time state — not a live dashboard.

## Correction (2026-07-26)

The Decision section originally stated that a manual "regenerate now" action bypasses the cooldown and is itself rate-limited per user. Neither was built: the shipped mechanism routes manual and automatic triggers through the same debounce and cap, queuing a manual request that arrives mid-window rather than exempting it. The text above has been corrected to describe the mechanism as implemented; no runtime behavior changed as part of this correction.
