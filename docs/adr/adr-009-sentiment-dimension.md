# ADR-009: Sentiment Dimension

**Date**: 2026-04-24
**Status**: Accepted

**Builds on**: [ADR-005](adr-005-health-dimension-framework.md)

## Context

Testing against the example account corpus surfaced a case where a single strongly negative message — a clear signal of account risk — caused an account's overall health score to *rise*. The engagement dimension is purely volume-based: one more message is one more message, regardless of what it says. Adding a negative signal moved the account into a higher engagement tier, and with sentiment not yet participating in the overall score, that was the only dimension pulling the number at all.

Investigation found the gap wasn't a missing feature — it was a database constraint out of sync with everything built on top of it. The narrative generator already computed a sentiment score for every narrative. Default configuration already defined a sentiment dimension with a real weight. Tests already covered writing and skipping the sentiment dimension score correctly. The one place sentiment didn't appear was the database's list of accepted dimension types — so every attempt to write a sentiment score was silently rejected at the constraint level, and no workspace had ever actually accumulated one.

## Decision

Add sentiment to the accepted set of dimension types, and rebalance the weights across engagement, sentiment, and manual override so that sentiment carries real, meaningful influence over the overall score rather than a token amount — reflecting that sentiment is the most direct available signal of account posture, while manual override remains present but no longer dominant.

When a sentiment score isn't yet available for a given scoring run — a first-time narrative, or a case where the model didn't return one — the dimension is skipped for that computation rather than defaulted to a neutral midpoint value. The remaining dimensions carry the overall score in the interim.

## Alternatives considered

**Derive sentiment on read, from the narrative, at query time instead of writing it as a stored dimension score.** Rejected for the same reason this was rejected for engagement in ADR-005: the overall-health calculation is a pure function over already-computed scores, with no database access of its own. Making it read live data would break that property and its testability.

**Default a missing sentiment score to neutral rather than skipping it.** Rejected. A missing score means "not yet assessed," not "acceptable." Silently substituting a neutral value would misrepresent an account whose sentiment may in fact be deteriorating. Skipping the dimension is the honest behavior — it lets the other dimensions carry the score until a real value exists.

**Recompute all historical rollups automatically as part of the fix.** Rejected — safer to let the next regularly scheduled scoring pass self-correct every account than to duplicate scoring logic inside a one-off migration.

## Consequences

**Positive**: overall health now reflects what an account's communication actually says, not just how much of it there is. The fix required no application code changes at all — everything needed was already built and tested; only the database's accepted values and the default weights needed correcting.

**Negative**: existing accounts show a temporary inconsistency — their overall score doesn't yet reflect the new weighting or a sentiment component — until each one's next regeneration cycle recomputes it under the corrected configuration.
