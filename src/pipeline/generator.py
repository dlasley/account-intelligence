import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anthropic
from postgrest.exceptions import APIError

import src.analytics as analytics
from src.config.loader import config_root
from src.config.schema import Config
from src.db.accounts import update_account_last_generated, update_account_overall_health
from src.db.audit import insert_audit_event
from src.db.dimension_configs import get_dimension_configs
from src.db.dimension_scores import (
    get_current_scores,
    insert_dimension_score,
    supersede_dimension_score,
)
from src.db.health_snapshots import insert_health_snapshot, supersede_health_snapshot
from src.db.narratives import (
    get_current_narrative,
    insert_narrative,
    supersede_current_narrative,
)
from src.domain.account import Account
from src.domain.contact import Contact, derive_display_name
from src.domain.dimension_config import DimensionConfig
from src.domain.dimension_score import DimensionScore, ScoredBy
from src.domain.events import ActorType, AuditAction
from src.domain.health_snapshot import HealthSnapshot
from src.domain.narrative import Narrative
from src.domain.signal import Direction, Signal, SourceType
from src.pipeline.confidence import determine_account_health, score_product_usage
from src.pipeline.health import compute_overall_health
from src.pipeline.product_usage_render import render_product_usage_trajectory
from supabase import Client

logger = logging.getLogger(__name__)

# Identifies the overall_score formula recorded in account_health_snapshots.
# Update this constant (and add a migration) when the weighting algorithm changes.
_HEALTH_FORMULA_VERSION = "weighted_average_v1"

VERTICAL_HINTS: dict[str, str] = {
    "software": (
        "Note product roadmap mentions, integration requests, and engineering bandwidth. "
        "Flag delivery slippage and competing-priority signals."
    ),
    "financial_services": (
        "Note compliance, audit, and regulatory references. "
        "Flag procurement-cycle and risk-review signals."
    ),
    "healthcare": (
        "Note clinical-workflow, HIPAA, and patient-care references. "
        "Flag credentialing or audit-cycle slippage."
    ),
    "life_sciences": (
        "Note regulatory, compliance, and clinical-trial references. Flag slipping milestones."
    ),
    "education": "Note grant cycles, publication timelines, and student researcher turnover.",
    "public_sector": (
        "Note legislative or agency-cycle references. Flag procurement-cycle signals."
    ),
    "retail_consumer": (
        "Note seasonal cycles, inventory references, and channel-partner mentions."
    ),
    "media_entertainment": (
        "Note editorial calendars, production cycles, and rights-management references."
    ),
    "manufacturing": (
        "Note supply-chain references, plant-floor language, and capital-procurement cycles."
    ),
    "energy_utilities": (
        "Note regulatory references, grid/operational language, and capital-project mentions."
    ),
    "professional_services": (
        "Note billable-hour and project-delivery language. Flag staff-utilization signals."
    ),
    "nonprofit": "Note grant cycles, donor or board references. Flag funding-cycle signals.",
}

_DIRECTION_LABELS: dict[Direction, str] = {
    Direction.INBOUND: "→ INBOUND",
    Direction.OUTBOUND: "← OUTBOUND",
    Direction.INTERNAL: "↔ INTERNAL",
}


@dataclass(frozen=True)
class GenerateResult:
    narrative: Narrative
    input_tokens: int
    output_tokens: int
    cached_tokens: int


def _prompt_version(template_content: str, model: str) -> str:
    payload = f"{model}:{template_content}"
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _body_excerpt(body: str, max_chars: int = 300) -> str:
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_space = truncated.rfind(" ")
    return truncated[:last_space] if last_space > 0 else truncated


def _render_signal_list(signals: list[Signal], contacts: dict[UUID, Contact]) -> str:
    lines = []
    for s in sorted(signals, key=lambda x: x.occurred_at, reverse=True):
        date = s.occurred_at.strftime("%Y-%m-%d")
        direction = _DIRECTION_LABELS.get(s.direction, str(s.direction))
        author = contacts.get(s.author_contact_id) if s.author_contact_id else None
        author_str = (
            f"{derive_display_name(author.display_name, author.email)} <{author.email}>"
            if author
            else "Unknown"
        )
        subject = s.subject or "(no subject)"
        excerpt = _body_excerpt(s.body)
        lines.append(f"[{date}] {direction} | {subject}\nFrom: {author_str}\n{excerpt}\n---")
    return "\n".join(lines) if lines else "No signals in window."


def _render_contact_summary(signals: list[Signal], contacts: dict[UUID, Contact]) -> str:
    counts: dict[UUID, int] = {}
    for s in signals:
        if s.author_contact_id:
            counts[s.author_contact_id] = counts.get(s.author_contact_id, 0) + 1
    lines = []
    for cid, count in sorted(counts.items(), key=lambda x: -x[1]):
        c = contacts.get(cid)
        if c and not c.is_internal:
            lines.append(
                f"- {derive_display_name(c.display_name, c.email)} <{c.email}>"
                f" — {count} signal{'s' if count != 1 else ''}"
            )
    return "\n".join(lines) if lines else "No external contacts identified."


def _render_valid_contact_list(contacts: dict[UUID, Contact]) -> str:
    """Build the VALID CONTACTS whitelist block for the narrative prompt.

    Includes all non-internal contacts known for the account (not just signal authors).
    Formatted as a simple list so the LLM can do exact-name matching.
    """
    external = [c for c in contacts.values() if not c.is_internal]
    if not external:
        return "No contacts identified."
    lines = [
        f"- {derive_display_name(c.display_name, c.email)} <{c.email}>"
        for c in external
    ]
    return "\n".join(lines)


def _load_template(config: Config) -> str:
    return (config_root() / config.narrative_generation.prompt_template_path).read_text()


def _resolve_prompt_variant(account_id: UUID) -> str:
    """Evaluate the `narrative-prompt-variant` PostHog flag for this account.

    Returns ``'v1'`` (control, current behavior) or ``'v2'`` (test variant)
    based on the flag's variant assignment, randomized per account. Defaults
    to ``'v1'`` if PostHog is disabled or the flag is undefined — guarantees
    backward-compatible behavior when PostHog isn't wired in (e.g., tests,
    local dev without POSTHOG_API_KEY).

    The variant assignment is consistent for a given account_id: an account
    always sees the same variant across regenerations, so trajectory analysis
    isn't muddied by mid-experiment switching.
    """
    from src.analytics import get_feature_flag

    variant = get_feature_flag(
        "narrative-prompt-variant", str(account_id), default="v1"
    )
    return variant if variant in ("v1", "v2") else "v1"


def _load_template_for_variant(config: Config, variant: str) -> str:
    """Load the prompt template for the resolved variant.

    Falls back to the configured (v1) path if the variant-specific file is
    missing — defensive against config drift or missing v2 file in some
    environments.
    """
    configured = config.narrative_generation.prompt_template_path
    if variant == "v2":
        variant_path = configured.replace("narrative.v1.md", "narrative.v2.md")
        full_path = config_root() / variant_path
        if full_path.exists():
            return full_path.read_text()
    return (config_root() / configured).read_text()


def _strip_fences(text: str) -> str:
    """Remove markdown code fences and strip prose preambles before JSON.

    Handles three response shapes:
    1. Entire response is one code fence → strip fence markers.
    2. Prose preamble, then fenced JSON → extract interior of first fence.
    3. Prose preamble, then raw JSON (no fence) → trim everything before first '{'.
    """
    text = text.strip()
    if text.startswith("```"):
        # Case 1: entire response is a single fence block.
        lines = text.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner).strip()

    fence_idx = text.find("```")
    if fence_idx != -1:
        # Case 2: prose preamble followed by a fenced block.
        logger.warning("_strip_fences: stripping prose preamble before code fence")
        after_fence = text[fence_idx:]
        lines = after_fence.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner).strip()

    brace_idx = text.find("{")
    if brace_idx > 0:
        # Case 3: prose preamble followed by raw JSON — trim to the first '{'.
        logger.warning("_strip_fences: stripping prose preamble before raw JSON")
        return text[brace_idx:].strip()

    return text


def _score_and_snapshot(
    narrative: Narrative,
    account: Account,
    signals: list[Signal],
    dimension_configs: list[DimensionConfig],
    client_db: Client,
    scored_at: datetime,
) -> None:
    email_cfg = next(
        (d for d in dimension_configs if d.dimension_type == "email" and d.enabled), None
    )
    if not email_cfg:
        logger.debug(
            "No enabled email dimension config for account %s — skipping scoring", account.slug
        )
        return

    score_from = email_cfg.config.get("email_score_source", "engagement")
    if score_from == "engagement":
        email_score = narrative.engagement
    elif score_from == "sentiment":
        if narrative.sentiment is None:
            logger.warning(
                "Email dimension score skipped for account %s: sentiment not available",
                account.slug,
            )
            return
        email_score = narrative.sentiment
    elif score_from == "composite":
        if narrative.sentiment is None:
            logger.warning(
                "Email dimension score skipped for account %s: sentiment not available"
                " for composite",
                account.slug,
            )
            return
        w_eng = float(email_cfg.config.get("engagement_weight", 0.6))
        w_sent = float(email_cfg.config.get("sentiment_weight", 0.4))
        raw = narrative.engagement * w_eng + narrative.sentiment * w_sent
        email_score = max(1, min(100, round(raw)))
    else:
        logger.warning(
            "Unknown score_from value '%s' for email dimension on account %s",
            score_from,
            account.slug,
        )
        return

    supersede_dimension_score(client_db, account.id, email_cfg.id, scored_at)
    insert_dimension_score(
        client_db,
        DimensionScore(
            id=uuid4(),
            workspace_id=account.workspace_id,
            account_id=account.id,
            dimension_id=email_cfg.id,
            score=email_score,
            rationale=f"Computed from narrative {narrative.id} ({score_from})",
            scored_by=ScoredBy.SYSTEM,
            metadata={"narrative_id": str(narrative.id), "score_from": score_from},
            scored_at=scored_at,
            superseded_at=None,
        ),
    )

    # Write sentiment dimension score if the dimension is configured
    sentiment_cfg = next(
        (d for d in dimension_configs if d.dimension_type == "sentiment" and d.enabled), None
    )
    if sentiment_cfg is not None:
        if narrative.sentiment is not None:
            supersede_dimension_score(client_db, account.id, sentiment_cfg.id, scored_at)
            insert_dimension_score(
                client_db,
                DimensionScore(
                    id=uuid4(),
                    workspace_id=account.workspace_id,
                    account_id=account.id,
                    dimension_id=sentiment_cfg.id,
                    score=narrative.sentiment,
                    rationale=f"LLM sentiment from narrative {narrative.id}",
                    scored_by=ScoredBy.SYSTEM,
                    metadata={"narrative_id": str(narrative.id)},
                    scored_at=scored_at,
                    superseded_at=None,
                ),
            )
        else:
            logger.warning(
                "Sentiment dimension config exists but narrative.sentiment is None"
                " for account %s — dimension score not written",
                account.slug,
            )

    # Write product_usage dimension score if the dimension is configured
    product_usage_cfg = next(
        (d for d in dimension_configs if d.dimension_type == "product_usage" and d.enabled), None
    )
    if product_usage_cfg is not None:
        product_score, window_days_used = score_product_usage(
            signals,
            product_usage_cfg.config,
            account.frequency_multiplier,
            now=scored_at,
        )
        if product_score is not None and window_days_used is not None:
            early_start = scored_at - timedelta(days=window_days_used)
            mid = scored_at - timedelta(days=window_days_used / 2)
            prod_signals = [
                s for s in signals
                if s.source_type == SourceType.PRODUCT_EVENT and s.occurred_at >= early_start
            ]
            recent_prod = [s for s in prod_signals if s.occurred_at >= mid]
            early_prod = [s for s in prod_signals if s.occurred_at < mid]
            recent_contacts = len({s.author_contact_id for s in recent_prod if s.author_contact_id})
            early_count = len(early_prod)
            half = window_days_used // 2
            ratio = len(recent_prod) / early_count if early_count > 0 else None
            cascade = product_usage_cfg.config.get(
                "window_days_cascade",
                [int(product_usage_cfg.config.get("window_days", 7))],
            )
            rationale = (
                f"Product usage: {len(recent_prod)} events/{recent_contacts} contacts"
                f" (recent {half}d) vs {early_count} events (prior {half}d)."
                + (
                    f" Trajectory ratio: {ratio:.2f}."
                    if ratio is not None
                    else " No prior baseline."
                )
                + (
                    f" Scored from {window_days_used}d window (cascade tier)."
                    if window_days_used != cascade[0]
                    else ""
                )
            )
            tiers_evaluated = (
                cascade.index(window_days_used) + 1 if window_days_used in cascade else 1
            )
            supersede_dimension_score(client_db, account.id, product_usage_cfg.id, scored_at)
            insert_dimension_score(
                client_db,
                DimensionScore(
                    id=uuid4(),
                    workspace_id=account.workspace_id,
                    account_id=account.id,
                    dimension_id=product_usage_cfg.id,
                    score=product_score,
                    rationale=rationale,
                    scored_by=ScoredBy.SYSTEM,
                    metadata={
                        "narrative_id": str(narrative.id),
                        "window_days": cascade[0],
                        "window_days_used": window_days_used,
                        "cascade_tiers_evaluated": tiers_evaluated,
                    },
                    scored_at=scored_at,
                    superseded_at=None,
                ),
            )
        else:
            logger.debug(
                "No product events in window for account %s — product_usage dimension skipped",
                account.slug,
            )

    all_scores = get_current_scores(client_db, account.workspace_id, account.id)
    dim_map_all = {d.id: d for d in dimension_configs}
    dim_map = {k: v for k, v in dim_map_all.items() if v.enabled}
    weighted = [
        (dim_map[s.dimension_id].weight, s.score) for s in all_scores if s.dimension_id in dim_map
    ]
    overall = compute_overall_health(weighted)

    supersede_health_snapshot(client_db, account.id, scored_at)
    all_score_map = {
        dim_map_all[s.dimension_id].dimension_type: s.score
        for s in all_scores
        if s.dimension_id in dim_map_all
    }
    insert_health_snapshot(
        client_db,
        HealthSnapshot(
            id=uuid4(),
            workspace_id=account.workspace_id,
            account_id=account.id,
            overall_score=overall,
            dimension_scores=all_score_map,
            formula_version=_HEALTH_FORMULA_VERSION,
            computed_at=scored_at,
            superseded_at=None,
        ),
    )

    update_account_overall_health(client_db, account.id, overall)


def generate_narrative(
    account: Account,
    signals: list[Signal],
    contacts: dict[UUID, Contact],
    prior_narrative: Narrative | None,
    config: Config,
    workspace_slug: str,
    client_db: Client,
    client_anthropic: anthropic.Anthropic,
) -> GenerateResult:
    now = datetime.now(UTC)

    # 1. Load dimension configs for post-narrative scoring
    dimension_configs = get_dimension_configs(client_db, account.workspace_id)

    # 2. Determine account health
    confidence = determine_account_health(
        signals, config.account_health, account.frequency_multiplier, now=now
    )

    # 3. Cap signals to max_signals_in_context most recent from the confidence window
    window_signals = sorted(confidence.signals_in_window, key=lambda s: s.occurred_at, reverse=True)
    capped = window_signals[: config.narrative_generation.max_signals_in_context]

    # 4. Resolve prompt variant via PostHog feature flag + load template.
    # Variant assignment is consistent per account_id (same account always gets
    # the same variant across regenerations). Defaults to 'v1' if PostHog is off.
    prompt_variant = _resolve_prompt_variant(account.id)
    template = _load_template_for_variant(config, prompt_variant)
    prompt_ver = _prompt_version(template, config.narrative_generation.model)

    vertical_hint_block = ""
    if config.narrative_generation.include_vertical_hint and account.vertical:
        hint = VERTICAL_HINTS.get(str(account.vertical), "")
        if hint:
            vertical_hint_block = f"**Vertical note**: {hint}"

    signal_list_text = _render_signal_list(capped, contacts)
    contact_summary_text = _render_contact_summary(capped, contacts)
    valid_contact_list_text = _render_valid_contact_list(contacts)
    prior_text = prior_narrative.narrative if prior_narrative else "No prior narrative."

    # Product usage trajectory block — pure function, no DB I/O (ADR-017 D5 Option C).
    # Reads dimension_configs loaded above; passes full config dict so render function
    # runs the same cascade tier selection as score_product_usage.
    product_usage_dim = next(
        (d for d in dimension_configs if d.dimension_type == "product_usage" and d.enabled), None
    )
    product_trajectory_block = render_product_usage_trajectory(
        signals,
        now,
        product_usage_dim.config if product_usage_dim else None,
    )

    # Split on the `---` separator: user part (variable context) / system part (static rules)
    parts = template.split("\n---\n", maxsplit=1)
    user_template = parts[0]
    system_template = parts[1] if len(parts) > 1 else ""

    replacements = {
        "{{account_name}}": account.name,
        "{{vertical}}": str(account.vertical) if account.vertical else "N/A",
        "{{account_status}}": str(account.status),
        "{{vertical_hint_block}}": vertical_hint_block,
        "{{engagement_label}}": confidence.tier_name.upper(),
        "{{engagement_score}}": str(confidence.score),
        "{{engagement_rationale}}": confidence.rationale,
        "{{signal_count}}": str(len(capped)),
        "{{signal_list}}": signal_list_text,
        "{{valid_contact_list}}": valid_contact_list_text,
        "{{contact_summary}}": contact_summary_text,
        "{{prior_narrative}}": prior_text,
        "{{product_usage_trajectory}}": (
            f"## Product Usage Trajectory\n\n{product_trajectory_block}\n\n"
            if product_trajectory_block
            else ""
        ),
    }

    def _fill(text: str) -> str:
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text

    user_prompt = _fill(user_template)
    system_prompt = (
        _fill(system_template)
        if system_template
        else (
            "You are an expert account analyst writing concise account health narratives."
        )
    )

    # 5. Call Claude API with prompt caching on the system block
    # Layer 1 audit evidence: tag the OTel span with workspace.slug so PostHog
    # LLM Analytics events are filterable by workspace even when content capture
    # is off (the default per SOC-2 control in src/observability/llm.py).
    # get_current_span() returns a no-op span when OTel is suppressed (e.g. in
    # pytest), so the is_recording() guard makes this branch free in tests.
    try:
        from opentelemetry import trace as _otel_trace

        _span = _otel_trace.get_current_span()
        if _span.is_recording():
            _span.set_attribute("workspace.slug", workspace_slug)
            _span.set_attribute("deploy_env", os.environ.get("DEPLOY_ENV", "development"))
            _span.set_attribute("llm_call_kind", "narrative")
            _span.set_attribute("prompt_variant", prompt_variant)
    except Exception:
        pass

    response = client_anthropic.messages.create(
        model=config.narrative_generation.model,
        # 4096: narratives can be long; 2048 caused mid-sentence truncation on the
        # first live simulator run. 4096 gives ~3x headroom within Opus 4.6's 8192
        # output limit (ADR-021 fix, 2026-05-09).
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # 6. Parse JSON response (retry once on JSONDecodeError — unescaped chars in
    #    interpolated email bodies occasionally produce structurally invalid JSON
    #    on the first stochastic sample; a fresh sample almost always resolves it).
    first_block = response.content[0]
    if not isinstance(first_block, anthropic.types.TextBlock):
        raise RuntimeError(
            f"Expected TextBlock from narrative model, got {type(first_block).__name__}"
        )
    raw = _strip_fences(first_block.text)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "Narrative JSON parse failed for %s (attempt 1/2), retrying:\n%s",
            account.slug,
            raw[:200],
        )
        retry_response = client_anthropic.messages.create(
            model=config.narrative_generation.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        retry_block = retry_response.content[0]
        if not isinstance(retry_block, anthropic.types.TextBlock):
            raise RuntimeError(  # noqa: B904
                f"Expected TextBlock from narrative model on retry,"
                f" got {type(retry_block).__name__}"
            )
        raw = _strip_fences(retry_block.text)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(
                "Narrative JSON parse failed for %s (attempt 2/2):\n%s", account.slug, raw[:200]
            )
            raise

    # Parse and clamp sentiment (LLM may return out-of-range or wrong type)
    raw_sentiment = parsed.get("sentiment")
    sentiment: int | None = None
    if isinstance(raw_sentiment, (int, float)):
        clamped = max(1, min(100, int(raw_sentiment)))
        if clamped != int(raw_sentiment):
            logger.warning(
                "Sentiment out of range for %s: %s → %s", account.slug, raw_sentiment, clamped
            )
        sentiment = clamped

    # 7. Supersede the current active narrative (if any)
    supersede_current_narrative(client_db, account.id, now)

    # 8. Build and insert the new narrative
    candidate = Narrative(
        id=uuid4(),
        workspace_id=account.workspace_id,
        account_id=account.id,
        narrative=parsed["narrative"],
        engagement=confidence.score,
        engagement_rationale=confidence.rationale,
        sentiment=sentiment,
        signal_window_start=confidence.window_start,
        signal_window_end=confidence.window_end,
        signals_considered=tuple(s.id for s in capped),
        model=config.narrative_generation.model,
        prompt_version=prompt_ver,
        generated_at=now,
        superseded_at=None,
    )
    try:
        narrative = insert_narrative(client_db, candidate)
    except APIError as exc:
        if exc.code == "23505":
            # Concurrent worker superseded + inserted between our two round-trips.
            # The winning narrative is already persisted — skip steps 8-9 and return it.
            logger.warning(
                "Narrative insert conflict for account %s — concurrent worker won, skipping.",
                account.slug,
            )
            existing = get_current_narrative(client_db, account.workspace_id, account.id)
            if existing is None:
                raise  # unexpected: no active narrative despite conflict
            cached_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            return GenerateResult(
                narrative=existing,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_tokens=cached_tokens,
            )
        raise

    # 9. Update the account's last_narrative_generated_at for rate-cap checks
    update_account_last_generated(client_db, account.id, now)

    # 10. Post-narrative health scoring (fire-and-log — never fails narrative generation)
    try:
        _score_and_snapshot(narrative, account, signals, dimension_configs, client_db, now)
    except Exception:
        logger.warning(
            "Post-narrative scoring failed for account %s — will retry on next regen",
            account.slug,
            exc_info=True,
        )

    # 10b. Analytics (fire-and-log — same pattern as post-narrative scoring)
    try:
        cached_tokens_for_analytics = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        analytics.track(
            "Narrative Generated",
            account.workspace_id,
            {
                "account_id": str(account.id),
                "model": narrative.model,
                "sentiment": narrative.sentiment,
                "engagement": narrative.engagement,
                "signal_count": len(narrative.signals_considered),
                "cached_tokens": cached_tokens_for_analytics,
            },
        )
    except Exception:
        logger.warning("analytics.track failed for Narrative Generated", exc_info=True)

    # 11. Audit
    insert_audit_event(
        client_db,
        workspace_id=account.workspace_id,
        actor_type=ActorType.WORKER,
        actor_id="worker",
        action=AuditAction.NARRATIVE_GENERATED,
        resource_type="narrative",
        resource_id=narrative.id,
        metadata={
            "account_id": str(account.id),
            "engagement": narrative.engagement,
            "sentiment": narrative.sentiment,
            "signal_count": len(narrative.signals_considered),
            "model": narrative.model,
            "prompt_version": narrative.prompt_version,
        },
    )

    cached_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    return GenerateResult(
        narrative=narrative,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_tokens=cached_tokens,
    )
