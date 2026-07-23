# ADR-013: Contact-Account Linkage on Ingest

**Date**: 2026-05-02
**Status**: Accepted

## Context

Contacts arrive through more than one ingestion path — inbound email and product-usage telemetry both create contact records for people the system hasn't seen before. Neither path linked a new contact to its account automatically, even when the contact's email domain was an unambiguous, already-indexed match against an account already in the system. For example, a contact arriving from `harvard.edu` should link automatically to an account whose registered domain is the same — but nothing in either ingestion path performed that check.

A one-time manual correction fixed the bulk of the existing data. But a second, compounding defect made that fix fragile: the underlying write was a blind upsert that overwrote the account link with whatever value the incoming event carried — which, for both ingestion paths, was always empty. Any correctly-assigned link, whether set manually or by a future automated fix, would be silently erased the next time that contact generated any new activity at all.

## Decision

Perform the domain match **at write time** — when a contact is created or updated — rather than lazily on read. A contact whose domain matches exactly one account gets linked automatically. A contact whose domain matches two or more accounts is left unassigned rather than guessed at; a wrong automatic link is worse than no link, since it would misattribute signals and pollute scoring for the wrong account. The raw upsert is replaced with a safe version that only ever overwrites the account link (or a contact's display name) with a genuine non-empty value — never with a blank, regardless of what the triggering event happened to carry.

## Alternatives considered

**Resolve the account link lazily, when a contact is viewed or used, rather than at ingestion.** Rejected. This would correctly resolve contacts on the pages that read them, but would leave any contact who hasn't triggered fresh activity since the fix permanently unlinked everywhere else — contact search, cross-account reporting, and any future health-scoring attribution. A write-time fix is a single point of correctness that benefits every context uniformly, not just the ones with recent activity.

**Surface ambiguous domain matches as a warning at ingestion time.** Rejected as scope creep for this fix. Both ingestion paths are asynchronous and already return success before any downstream resolution happens; adding a live ambiguity signal at that point is a real feature (a "needs assignment" queue) that doesn't yet have a home in the product. Noted as a natural next step once one exists.

**Use an elevated, RLS-bypassing database function for the safe upsert.** Rejected. This is a data-integrity concern, not an authorization concern — the fix should run under the exact same access checks as any other write to the same table, preserving the defense-in-depth the rest of the schema relies on rather than carving out an exception for convenience.

## Consequences

**Positive**: new contacts resolve to the correct account automatically whenever their domain match is unambiguous, and a correctly-assigned link — whether set by a person or resolved automatically — can no longer be silently erased by a later, less-informed write.

**Negative**: contacts whose domain matches multiple accounts still require manual assignment, since no dedicated interface for that queue exists yet. Contacts created before the fix that haven't generated any new activity since remain unlinked until they do — this self-heals over time rather than requiring a further backfill.
