# Signal Routing

How inbound email and product-telemetry events are routed to a workspace and an account. Reference for debugging, extension, and onboarding.

This is the routing layer; for the wider architecture (worker, frontend, audit harness, RLS, etc.) see [architecture.md](architecture.md).

---

## Domain transition state (read this first)

The codebase is mid-migration from the legacy domain to the target domain. **Live infrastructure (DNS / SendGrid / Cloud Run env vars) still uses the legacy domain**; **code references and tests use the target domain**.

| Surface | Today (live MX + worker `INBOUND_DOMAIN`) | Target state (after DNS + SendGrid + Cloud Run reconfig) |
|---|---|---|
| Email inbound MX | `<legacy-inbound-domain>` | `<target-inbound-domain>` |
| Tracker beacon URL fallback | (legacy) | `https://<target-api-domain>/event` |

This document uses the **target domain** for examples (`<target-inbound-domain>`, `<target-api-domain>`) for forward-compatibility. **Real forwarding tests today must use the legacy domain** — `<workspace>@<legacy-inbound-domain>`. The reconfig is a deferred operational task, tracked internally.

---

## Email signal path

**Endpoint**: `POST /inbound` on the FastAPI worker (Cloud Run). SendGrid Inbound Parse fires this webhook for every email arriving at the configured MX target.

### Address format

```
<workspace-slug>@<inbound-domain>
<workspace-slug>+<account-slug>@<inbound-domain>
```

The local part of the recipient address (left of `@`) names the workspace. An optional `+<account-slug>` suffix routes directly to a specific account (plus-addressing).

Examples:

| Recipient | Resolves to |
|---|---|
| `quantas-labs@<target-inbound-domain>` | workspace `quantas-labs`, no account hint |
| `quantas-labs+harvard@<target-inbound-domain>` | workspace `quantas-labs`, account hint `harvard` |
| `lattice-build+crucible@<target-inbound-domain>` | workspace `lattice-build`, account hint `crucible` |
| `lattice-build-thornfield-ai@<target-inbound-domain>` | **workspace lookup for `lattice-build-thornfield-ai` — fails (no such workspace); email rejected.** Hyphen has no special meaning to the parser. |

Parsing logic at [src/signals/shared_inbox.py:75-91](../src/signals/shared_inbox.py#L75-L91). The parser checks for `+` in the local part. If present, it partitions into workspace/account; if absent, the entire local part is the workspace slug.

### Router cascade

[src/pipeline/router.py](../src/pipeline/router.py) is a pure function (no DB calls). It walks six numbered stages in order. The first match wins; a match returns a `RoutingResult` with an account_id, a `RoutingMethod` enum value, and a confidence score in `[0.0, 1.0]`.

| # | Stage | Matches when | Confidence | RoutingMethod |
|---|---|---|---|---|
| 0 | `outbound_bcc` | `from_email` is in `workspace.internal_domains` (this email was sent BY workspace staff) | 0.9 | `OUTBOUND_BCC` |
| 1 | `plus_addressing` | recipient had `+<account-slug>` AND a matching account exists | **1.0** | `PLUS_ADDRESSING` |
| 2 | `header_domain` | sender's email domain matches some active account's `primary_domain` | 0.9 | `HEADER_DOMAIN` |
| 3 | `forward_parse` | email looks like a forward (e.g., body contains `From:` / `Forwarded message` markers); extracted original sender's domain matches an active account | 0.7 | `FORWARD_PARSE` |
| 4 | `thread_inherit` | a `thread_id` (in-reply-to / references) matches an existing signal's thread → inherit that signal's account | 0.6 | `THREAD_INHERIT` (or `THREAD_INHERIT_SPLIT` if the thread spans multiple accounts) |
| 5 | `auto_discovery` | sender domain is **non-personal AND non-internal**; no earlier stage matched | 0.3 | `AUTO_DISCOVERY` (creates a candidate) |
| — | (fallback) | none of the above | 0.0 | `UNMATCHED` |

Stage 5 is implemented at [src/pipeline/router.py:262-296](../src/pipeline/router.py#L262-L296).

### Auto-discovery rules (stage 5)

Stage 5 fires only when:

- `from_email` is present
- `from_email`'s domain is **not** in `workspace.internal_domains` (would be stage 0 territory anyway, but the explicit check guards against bypass cases)
- `from_email`'s domain is **not** in `personal_provider_domains` (gmail, yahoo, outlook, hotmail, etc.)

When it fires, it constructs an `Account` with:

- `id = uuid5(NAMESPACE_DNS, f"{workspace.id}:auto:{from_domain}")` — **deterministic**. A second email from the same domain reuses this UUID, so candidates don't duplicate; the second signal attaches to the existing candidate.
- `slug = first segment of from_domain, lowercased` (e.g., `recursionpharma.com` → `recursionpharma`)
- `name = first segment of from_domain, title-cased` (e.g., `Recursionpharma`)
- `primary_domain = from_domain`
- `status = AccountStatus.CANDIDATE`

The candidate then surfaces in the frontend's CandidateSidebar with **Confirm** (promote to `status='active'`) or **Reject** (soft-delete via `deleted_at`) buttons.

The auto-derived slug + name often need editing before confirmation (e.g., `recursionpharma` is the slug but the customer's brand is "Recursion Pharmaceuticals"). The product currently doesn't surface a name-edit UI between candidate creation and confirmation — Confirm promotes as-is. Future product gap.

### Confirm / Reject mechanics (today)

The CandidateSidebar component calls SECURITY DEFINER RPCs (`activate_candidate_account`, `dismiss_candidate_account`) to flip `status` or set `deleted_at`. ADR-019 / migration 000027 replaced the earlier raw PostgREST `PATCH /rest/v1/accounts` writes with this pattern; `authenticated` role's table surface is now SELECT-only.

### Cross-workspace candidate behavior

Candidate creation is **scoped to the workspace the email was sent to**. The recipient address determines workspace; the sender domain only determines the candidate's name + domain within that workspace. Two consequences:

- An unknown prospect emailing two workspace-specific addresses (e.g., `acme-enterprise@...` and `acme-smb@...`) creates **two independent candidates** — one per workspace. There's no org-aware dedup.
- The candidate's deterministic UUID is namespaced by `workspace.id`, so the same domain producing candidates in two workspaces gives two distinct UUIDs.

For multi-workspace organizations, this cross-workspace duplication is a known gap, tracked internally as a deferred (not blocking) item — multi-workspace organization architecture.

---

## Product event signal path

**Endpoint**: `POST /event` on the FastAPI worker (renamed from `/ingest` on 2026-05-08 — privacy-aware naming, lower ad-blocker filter-list rate, matches Plausible's `/api/event` convention; the API key scope name remains `"ingest"` as an internal auth identifier). Called by the embeddable browser bundle (`src/server/static/event.js`, built via `npm run build:event-js`) running on the customer's product surface, OR by direct integrations (Segment-shape payloads supported; ADR-012).

**Auth**: `Authorization: Bearer <key>` checked against the `api_keys` table. Keys with the `pk_live_` prefix carry the `ingest` scope; the key's `workspace_id` column establishes which workspace the events belong to. Workspace identity is **established by the API key**, not by anything in the payload.

### Routing rules (no cascade — three flat cases)

[src/pipeline/product_event.py:60-91](../src/pipeline/product_event.py#L60-L91) — `normalize_product_event`:

| Has `contact_email`? | Contact resolution | RoutingMethod | account_id outcome |
|---|---|---|---|
| Yes; contact already exists at this workspace | use existing contact's account | `API_KEY_IDENTITY` | inherits from contact |
| Yes; contact does not exist; email-domain matches an **active** account's `primary_domain` | create new contact at that account | `AUTO_DISCOVERY` | the matching active account's id |
| Yes; contact does not exist; email-domain matches no active account | new contact created with `account_id=NULL`; signal also `account_id=NULL` | `AUTO_DISCOVERY` | `NULL` (orphaned) |
| Email missing | no contact created | `UNMATCHED` | `NULL` |

**Note**: the `AUTO_DISCOVERY` routing method here means "discover the existing active account by domain." It does NOT create a candidate account. There is no equivalent of the email cascade's stage 5 in the product event path.

### Asymmetry vs the email path

| Scenario | Email path (`/inbound`) | Product event path (`/event`) |
|---|---|---|
| Known contact / known account | normal route | normal route |
| Unknown contact, **domain matches active account** | normal route (header-domain match) | new contact created at that account; signal attached |
| Unknown contact, **unknown domain** (non-personal) | **stage 5 creates a CANDIDATE** + Confirm/Reject UI | **unmatched** — signal `account_id=NULL`, no UI surface |
| Personal-provider email (gmail / yahoo / etc.) | unmatched | not directly excluded; if `gmail.com` matches no active account → unmatched anyway |
| No email at all | (workspace identity is in the recipient envelope) | unmatched, no contact created |

The product event path **never creates candidate accounts** for unknown domains. This was a deliberate design choice (product telemetry assumes the customer is already onboarded), but it leaves a real gap for product-led-growth flows: a self-serve sign-up where a prospect lands in the customer's app should ideally surface as a candidate. Captured internally as a deferred future-ADR item.

### Browser bundle defaults

The embeddable browser bundle (`src/server/static/event.js`, built via `npm run build:event-js`; served at `/event.js` — renamed from `/tracker.js` on 2026-05-08, same privacy-aware reasoning as the endpoint rename — from the built output at `src/server/static/dist/event.js`) reads `data-key` and `data-url` attributes from its `<script>` tag. If `data-url` is absent, it falls back to **`https://<target-api-domain>/event`** (target state in code; legacy DNS not yet pointed there — see top of doc). Customers can override `data-url` to point at any environment-specific worker.

---

## Structured signal path (Plain / Pylon / Granola)

A third and fourth signal category — ticket and meeting-note records pushed or pulled from Plain, Pylon, and Granola — routes on a different identity model than either path above: workspace identity comes from the `external_credentials` row the request authenticates against (a per-workspace webhook secret or API key), not from an envelope address or an `Authorization: Bearer` API key against the `api_keys` table. Within that workspace, contact/account resolution mirrors the product-event path's 3-way logic exactly (known contact → matched; new contact whose email domain matches an active account → auto-linked; no domain match → orphaned; no participant email → unmatched) — no candidate-account creation, same as product events. See [architecture.md § Structured Signal Integrations](architecture.md#structured-signal-integrations) for the full push (`POST /signal/{kind}`) and poll (`POST /run-polls`) treatment; it isn't duplicated here since this document's scope is the email and product-event cascades specifically.

---

## Side-by-side comparison

| Property | Email path | Product event path |
|---|---|---|
| Endpoint | `POST /inbound` | `POST /event` |
| Workspace identity | recipient address local part (`<slug>@...`) | API key (`pk_live_*` row in `api_keys`) |
| Auth | SendGrid webhook signature | `Authorization: Bearer <key>` + scope check |
| Account routing | 6-stage cascade | 3 flat rules |
| Creates candidates? | **Yes (stage 5)** | **No** |
| Confidence score | per stage, `0.0`–`1.0` | binary: matched-known / matched-by-domain / unmatched |
| Idempotency | dedup on `external_id` (RFC 5322 `Message-ID`) | dedup on `event_id` if provided |

---

## Common edge cases / pitfalls

1. **Hyphenated address mistake**: `<workspace>-<account>@...` does NOT route via plus-addressing. The parser only recognizes `+`. Hyphens in the local part are part of the workspace slug. An address like `lattice-build-thornfield-ai@...` causes a workspace lookup for `lattice-build-thornfield-ai`, which fails and drops the email.
2. **Plus-addressing without a matching account**: stage 1 only matches if the named account exists in the workspace. If not, stage 1 returns no match and the cascade continues to stages 2-5. The plus-addressed slug is treated as a hint, not a hard constraint.
3. **Auto-discovery on a known domain**: stage 2 (header domain match) catches sender domains that match an existing **active** account. Stage 5 only fires if no earlier stage matched. So a sender from `thornfield.ai` (which is `thornfield-ai`'s primary domain in lattice-build) routes to `thornfield-ai` via stage 2, never reaching stage 5. Stage 5 is for genuinely new domains.
4. **Personal-provider domain blind spot**: emails from `randomperson@gmail.com` to a workspace's address get **unmatched**. There's no candidate created (stage 5 skips), and the signal lands orphaned. Real prospects often forward from personal addresses; this is a friction worth knowing.
5. **`auth.uid()` NULL fall-through in SECURITY DEFINER gates**: when writing routing-related stored functions, gate checks like `IF NOT (SELECT is_super_user FROM users WHERE id = auth.uid())` have a NULL fall-through if the caller is anonymous. Always wrap with `COALESCE(..., false)`. See CLAUDE.md "Common Patterns" → "SECURITY DEFINER function discipline."

---

## References

- Code:
  - [src/signals/shared_inbox.py](../src/signals/shared_inbox.py) — recipient address parsing
  - [src/pipeline/router.py](../src/pipeline/router.py) — email cascade (6 stages)
  - [src/pipeline/product_event.py](../src/pipeline/product_event.py) — product event normalization + routing
  - [src/server/routes/inbound.py](../src/server/routes/inbound.py) — `POST /inbound` handler
  - [src/server/routes/event.py](../src/server/routes/event.py) — `POST /event` handler
  - [src/server/static/event.js](../src/server/static/event.js) — embeddable browser script (served at `/event.js`)
- ADRs:
  - [ADR-001](adr/adr-001-inbound-mail-provider.md) — inbound mail provider choice (SendGrid) — public
  - ADR-012 — product telemetry ingest contract (unpublished, internal only)
  - [ADR-013](adr/adr-013-contact-account-linkage.md) — contact-account linkage (auto-discovery for product events) — public
  - ADR-019 — single mutation surface, replaces Confirm/Reject PATCH writes with RPCs (unpublished, internal only)
- Project docs:
  - [architecture.md](architecture.md) — wider system architecture
  - [../CLAUDE.md](../CLAUDE.md) — coding conventions including SECURITY DEFINER discipline
  - Progress tracker — deferred items including PLG signal coverage gap and multi-workspace org architecture (kept internally, not included in this repo)
