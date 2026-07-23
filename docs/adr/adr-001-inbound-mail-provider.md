# ADR-001: Inbound Mail Provider

**Date**: 2026-04-22
**Status**: Accepted

## Context

The platform turns email into account signal by receiving mail sent to a per-workspace address and routing it into the pipeline. That requires an inbound email provider: something that accepts mail on our behalf, parses it, and delivers the content to our service over a webhook.

Three kinds of provider were evaluated: a primarily-outbound platform with an inbound add-on, an inbound-focused specialist, and a roll-your-own combination of generic cloud messaging primitives (a mail relay plus a queue plus object storage).

One clarifying constraint shaped the comparison: the pipeline stores the provider's parsed payload for idempotency and replay, not the raw email format. Raw-MIME fidelity — a selling point of the inbound specialist — turned out not to matter for this system.

## Decision

Use a primarily-outbound email platform's inbound-parsing add-on, behind a small internal abstraction so the provider can be swapped without touching routing or narrative-generation code.

## Alternatives considered

**Inbound-focused specialist provider.** Purpose-built for receiving mail, with strong deliverability and both raw and parsed delivery formats. Rejected because its inbound tier requires a paid plan with no meaningful cost advantage over the alternative, and because raw-MIME delivery — its main differentiator — solves a problem this system doesn't have.

**Roll-your-own on generic cloud primitives (mail relay + queue + object storage).** The cheapest option at high volume and the most control over the raw message. Rejected for v1: it requires assembling and operating three separate services plus hand-rolled MIME parsing, which is disproportionate operational overhead at current volume. Noted as the right answer to revisit if inbound volume grows an order of magnitude.

**No provider — accept mail directly.** Not seriously considered; running a mail server is a much larger operational commitment than any of the above for no corresponding benefit.

## Consequences

**Positive**: bundled pricing rather than per-service billing; a large, well-documented ecosystem, which matters for a first-time integration; and a swappable abstraction means the choice isn't a lock-in.

**Negative / risk**: the provider's spam filtering can silently drop inbound messages unless explicitly configured to pass everything through — this is an operational setup step, not a code safeguard, and it has to be verified before go-live for any new deployment. Inbound parsing is also a secondary feature on a platform whose primary business is outbound sending, which is a real (if modest) support-quality risk to carry.

**Deferred**: revisit the roll-your-own approach if inbound volume grows large enough that per-message provider cost becomes material. The internal abstraction this decision established means that swap doesn't require touching anything downstream of ingestion.
