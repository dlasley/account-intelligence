# ADR-023: Narrative Prompt Variant Gating via Sticky Feature Flag

**Date**: 2026-07-07
**Status**: Accepted — mechanism implemented; activation gated on the precondition below

## Context

Narrative generation was extended to select between two prompt templates at generation time, based on a feature flag evaluated per account. This is the first time prompt *content* — not just the wording of a single active prompt, which had until now always been tuned in place — became something the system could run two versions of side by side and compare.

This earns a design record of its own, distinct from documenting what the code does, because of the reasoning behind three choices that will outlive this specific pair of prompt variants and get reused whenever the next one is added, or whenever this same pattern is applied to another prompt-driven surface in the product: why gate on a per-account-sticky flag rather than a simpler toggle; what "safe by default" needs to mean for a flag that selects prompt content, specifically; and how a multi-variant world interacts with this project's existing rule that narrative quality is verified before it ships — a rule that was written assuming a single active prompt, and needed an explicit answer for what happens once there's more than one.

## Decision

Gate narrative prompt content on a feature flag evaluated per account, using a stable per-account identifier so that a given account is consistently assigned to the same variant across every regeneration, rather than flapping between variants over time. Default to the known-good current prompt whenever the flag system is unreachable, the flag doesn't exist yet, or it resolves to an unrecognized value — a partial failure at any layer degrades to the same known-good behavior rather than an unhandled error. Tag the resolved variant on the underlying model call so downstream analytics can be sliced by variant without any separate correlation step.

Alongside the mechanism, this decision establishes new governance for the multi-variant case: **each variant must independently clear this project's existing narrative-quality bar, on a representative sample at realistic corpus density, before it is exposed to any live account traffic through the flag.** Shipping the code that is capable of selecting a variant is not the same thing as that variant being cleared to run.

## Alternatives considered

**An environment-variable or deploy-time toggle.** Rejected — it selects one variant for the entire deployment, not per account, so there's no way to run two variants side by side and compare them. Changing it also requires a redeploy, which rules out any kind of gradual rollout.

**A per-workspace configuration override**, reusing the project's existing workspace-level config system. Rejected on two grounds. First, the current account base doesn't have enough independent workspaces for a workspace-level split to produce a meaningful comparison. Second, and more durably: workspace configuration exists to express a customer's stable, intentional product preferences — not a randomized experiment assignment. Conflating the two would make a workspace's configuration illegible as "what this customer actually asked for."

**Ship the new variant as a straight replacement once it looks better on manual review.** Rejected — this is exactly the informal, unscored judgment call this project has already moved away from for narrative quality generally, in favor of an independent, repeatable quality check. A straight replacement also has no rollback lever if the new variant turns out worse in aggregate after the fact.

**A separate deployment or branch per variant.** Rejected — doubles the deployment surface for what is fundamentally a content change, and breaks the single-deployment shape the rest of the architecture assumes.

## Consequences

**Positive**: this establishes a reusable pattern — any future prompt experiment on any surface should use the same sticky, per-entity, flag-gated shape unless there's a documented reason not to, and a new hardcoded prompt-selection toggle introduced after this decision is a flag for review, not a green light. Sticky per-account assignment also supports clean before/after analysis over time, since an account's assigned variant doesn't drift mid-comparison.

**Negative**: sticky assignment is a deliberate one-way door per account — reassigning an account to see the other variant mid-comparison is possible but muddies any trend analysis for that account, so it isn't a casual operation. The mechanism that tags the resolved variant for analytics is currently a single point of failure with no regression test guarding it — a future refactor could silently drop that tag with no functional test failure, only a quiet loss of the ability to slice results by variant. And the current account base is small enough that any comparison run against it today should be read as validating the mechanism works, not as a statistically confident answer to which variant is actually better — a real go/no-go call needs a larger sample, decided on before results are read, not after.
