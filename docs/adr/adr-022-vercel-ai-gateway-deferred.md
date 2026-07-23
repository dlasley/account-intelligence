# ADR-022: Vercel AI Gateway — Deferred

**Date**: 2026-05-21
**Status**: Accepted (decision: defer indefinitely, with explicit re-evaluation conditions)

## Context

Vercel AI Gateway offers a unified routing layer across LLM providers — automatic fallback between models, centralized cost tracking, and a single observability surface — available to any Vercel-hosted project. It surfaced as a candidate worth adopting early, on the reasoning that retrofitting a gateway after a feature has shipped is more painful than building on top of it from day one.

The question this ADR actually answers isn't "is this a good product" — it evidently is, for the right architecture — but whether *this* project's architecture benefits from it today.

## Decision

**Defer adoption indefinitely.** No setup, no code change, no new environment configuration — with explicit conditions, below, that would reopen the question.

The reasoning: every LLM call in this system today happens inside a single backend worker service, which is also where the data context those calls depend on lives (account history, prior signals, workspace configuration). The frontend has no direct path to that context, so any future frontend-facing AI feature would still have to go through the same worker — meaning "frontend AI feature," in this architecture, actually means *frontend triggers the worker, the worker makes the call*. A gateway placed in front of the frontend would have nothing to route, because the frontend isn't the thing making LLM calls.

That reframes the real question as whether the worker's *existing* LLM calls should route through a gateway. Both current call sites — narrative generation and the cross-vendor audit check — are single-provider, latency-insensitive, and already observable through the project's own instrumentation. One of them specifically depends on committing to a fixed, known vendor as part of its own design; a gateway's automatic-fallback behavior would work directly against that guarantee if it silently retried against a different provider. Neither call site has an architectural trigger to adopt gateway routing today.

## Alternatives considered

**Set up the gateway in the frontend now, ahead of any concrete need, so the first frontend AI feature lands "gateway-native" by default.** This was the initial framing, and it carried a hidden assumption worth naming: that a future frontend AI feature would call an LLM directly from the frontend. Once that assumption was challenged, the case fell apart — nothing in this architecture makes that call from the frontend, so there'd be nothing for a frontend-side gateway to intercept.

**Route the backend's current LLM calls through the gateway now.** Considered and rejected for the reasons above — neither current call site gains meaningful value from routing, cost tracking, or fallback, and one of them actively needs to avoid silent provider fallback.

## Consequences

**Positive**: no new vendor relationship for a pure routing concern, no migration risk against an existing prompt-caching setup, and no added configuration discipline on the one call site that specifically must not fail over silently to another provider.

**Negative**: this declines the platform's own stated default recommendation for new projects. If a genuinely gateway-shaped need arises later, adopting it at that point is judged to be modest, non-zero work — not a refactor, but not free either.

## Re-evaluation triggers

This decision is revisited, not treated as permanent, if any of the following occurs: a future feature needs low-latency streaming from the frontend directly (pushing an LLM call out of the backend worker for the first time); a genuinely frontend-only AI feature emerges that needs no account-specific data context; a real need arises for the backend to gracefully fail over between providers for uptime reasons; a mandate emerges for a single unified LLM observability surface that the project's current instrumentation doesn't satisfy; or the project accumulates enough independent LLM call sites that centralized cost-guardrail management becomes preferable to per-site configuration.
