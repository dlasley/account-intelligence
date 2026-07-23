# ADR-010: Outreach Templates (Replacing LLM-Generated Drafts)

**Date**: 2026-04-25
**Status**: Accepted — supersedes the original generation approach described below

## Context

Draft outreach lets a person request a starting-point email for an account, edit it, and send it without leaving the product. The first version of this feature generated that draft with an LLM, grounded in the account's narrative and recent signals, with retrieval-augmented generation deliberately deferred as unnecessary at this scale — the relevant context fit comfortably in a single prompt.

Testing surfaced a serious problem: the model invented specifics that weren't present in any underlying signal — relationship details, references to activity that hadn't happened — despite explicit instructions not to. The guardrail was followed in spirit and still failed in practice. Sending fabricated, unreviewed content in a customer-facing email is not an acceptable risk for this product, no matter how good the prompt engineering gets.

## Decision

Remove LLM-generated drafting from the outreach path entirely. Replace it with a library of structured templates — plain text with explicit `[placeholder]` slots the user must fill in themselves — presented alongside a panel of the account's most recent signals for reference. A simple, fully deterministic recommender (rule-based, no model call) suggests which template best fits the account's current state, based on signals like health score and time since last contact, with the reasoning behind each suggestion shown to the user.

## Alternatives considered

**Keep LLM generation, with tighter grounding and stronger guardrails.** Rejected. The finding wasn't that the prompt was insufficiently careful — it was that guardrails alone don't reliably prevent a generative model from fabricating specifics in this kind of open-ended writing task. The risk of a single hallucinated fact reaching a customer's inbox outweighs any drafting-speed benefit, however well-tuned the prompt.

**Use a model to power the template recommendation as well.** Rejected. Choosing which template fits best only requires a handful of already-known, deterministic signals — there's no ambiguity a model resolves better than a short rule list, and adding a model call there would introduce cost and a second failure mode for no accuracy gain.

## Consequences

**Positive**: zero hallucination risk in the outreach path — no generated prose reaches the send button unreviewed, only structural prose and explicit gaps the user fills with real specifics they know to be true. No model call in this path at all means no added latency or cost here. Template content can be edited directly without touching application code. The send flow itself — delivery, capturing sent mail back into the account's history — is completely unaffected by this change.

**Negative**: the rule-based recommender's keyword-based risk detection is prone to false positives (a phrase like "not concerned" can still match on "concerned"). Template content changes currently require a deploy, which is workable at this stage but would need a friendlier editing surface at larger scale. A user who ignores the placeholder markers can still send a visibly incomplete-looking email — mitigated with a UI warning, not a hard block.
