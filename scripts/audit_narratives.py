"""Cross-model narrative audit harness CLI.

Evaluates narrative quality using GPT-5-mini as an independent auditor
(cross-vendor from the Anthropic-generated narratives — ADR-016 D1).

The script loads ``.env`` automatically via python-dotenv, so the legacy
``uv run --env-file .env`` invocation is no longer required.

Usage:
    uv run python scripts/audit_narratives.py --help
    uv run python scripts/audit_narratives.py --dry-run
    uv run python scripts/audit_narratives.py --narrative-id <uuid> --write-db
    uv run python scripts/audit_narratives.py --limit 5 --audit-source nightly --write-db
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openai
from dotenv import load_dotenv

from src.domain.contact import derive_display_name

# Load .env from the repo root if present so the script can be run as
# `uv run python scripts/audit_narratives.py ...` without `--env-file`.
# A no-op if the file does not exist; existing process env vars win.
load_dotenv()

# Add repo root to sys.path so src.* imports work when the script is invoked
# directly (without `uv run` or `pip install -e .`).
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pinned model snapshot — update deliberately (ADR-016 D1).
#: Upgrade path: gpt-5 full model at ~5x cost (~$0.275/run) if quality issues emerge.
AUDITOR_MODEL = "gpt-5-mini-2025-08-07"

#: GPT-5-mini pricing (ADR-016 D1 + D8): $0.25/M input, $2.00/M output.
#: Output rate applies to both reasoning tokens and content tokens.
_PRICING: dict[str, float] = {
    "input_per_million": 0.25,
    "output_per_million": 2.00,
}

#: Per-narrative cost estimate used for pre-run ceiling check (ADR-016 D8).
_PER_NARRATIVE_ESTIMATE_USD = 0.006

#: Estimated upper bound on the audit corpus when --limit is not set.
#: Used by the cost-ceiling check before any DB fetch. The ceiling guard fires
#: pre-fetch, so this value must track the actual fixture corpus size — bump it
#: when new scenarios land in fixtures/synthetic-scenarios/ or pilot opt-in
#: expands the corpus.
#:
#: Corpus size estimate: the simulator's per-week mode generates per-account narratives
#: and audits only the final week by default. lattice-build = 12 accounts; synth-trio
#: workspaces = 1 account each. Total active narratives at any one time depends on which
#: workspaces have been simulated; the conservative upper bound for a full demo refresh
#: is ~15 narratives audited per cycle.
_DEFAULT_CORPUS_SIZE_ESTIMATE = 15

#: Blocking criteria — any failure blocks the run (ADR-016 D6).
_BLOCKING_CRITERIA = {"faithfulness", "coverage", "calibration", "hallucination"}

#: Advisory criteria — recorded but do not block (ADR-016 D6).
_ADVISORY_CRITERIA = {"tone_fit"}

_PROMPT_PATH = Path(__file__).parent / "prompts" / "audit-narratives.md"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class CriterionResult:
    criterion: str
    passed: bool
    score: int | None  # 1-5 for scored criteria; None for binary
    reasoning: str
    details: dict


@dataclass
class AuditResult:
    faithfulness: CriterionResult
    coverage: CriterionResult
    calibration: CriterionResult
    hallucination: CriterionResult
    tone_fit: CriterionResult
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class GateOutcome:
    overall_passed: bool
    hard_gate_failures: int
    advisory_failures: int
    failure_criteria: list[str] = field(default_factory=list)
    warning_criteria: list[str] = field(default_factory=list)


@dataclass
class AuditContext:
    """All per-narrative context the auditor needs — every field required.

    Making these required (no ``| None = None`` defaults) means any caller that
    omits a field fails at type-check time, not silently at audit-quality time.
    Pass ``[]`` / ``{}`` explicitly when the value is genuinely absent.
    """

    signals: list[Any]          # Signal objects for this narrative's window
    contacts: list[dict]        # Account roster (pass [] if account has none)
    account_meta: dict          # name, vertical, status, primary_domain, additional_domains
    dimension_configs: list[dict]  # [{dimension_type, weight}, ...] for enabled dims
    product_usage_config: dict  # health_dimension_configs.config for product_usage dim
    workspace_slug: str         # For SOC-2 Layer 1 audit-trail OTel span attribute


# ---------------------------------------------------------------------------
# Pure functions: parsing, gate evaluation, cost
# ---------------------------------------------------------------------------


def parse_audit_response(raw_dict: dict) -> AuditResult:
    """Parse the structured-output JSON dict from GPT-5 into an AuditResult.

    Strict validation: raises ValueError on missing/extra/wrong-typed criteria.
    The raw_dict shape must match the output schema in scripts/prompts/audit-narratives.md.
    """
    _REQUIRED_CRITERIA = {"faithfulness", "coverage", "calibration", "hallucination", "tone_fit"}

    present = set(raw_dict.keys())
    missing = _REQUIRED_CRITERIA - present
    extra = present - _REQUIRED_CRITERIA
    if missing:
        raise ValueError(f"Missing criteria in audit response: {sorted(missing)}")
    if extra:
        raise ValueError(f"Unknown criteria in audit response: {sorted(extra)}")

    def _parse_criterion(name: str, data: dict) -> CriterionResult:
        if not isinstance(data, dict):
            raise TypeError(f"Criterion '{name}' must be a dict, got {type(data)}")
        score = data.get("score")
        if score is not None and not isinstance(score, int):
            raise TypeError(f"Criterion '{name}' score must be int or None, got {type(score)}")
        passed = data.get("passed")
        if not isinstance(passed, bool):
            raise TypeError(f"Criterion '{name}' passed must be bool, got {type(passed)}")
        reasoning = data.get("reasoning", "")
        if not isinstance(reasoning, str):
            raise TypeError(f"Criterion '{name}' reasoning must be str")
        details = data.get("details", {})
        if not isinstance(details, dict):
            raise TypeError(f"Criterion '{name}' details must be dict")
        return CriterionResult(
            criterion=name,
            passed=passed,
            score=score,
            reasoning=reasoning,
            details=details,
        )

    return AuditResult(
        faithfulness=_parse_criterion("faithfulness", raw_dict["faithfulness"]),
        coverage=_parse_criterion("coverage", raw_dict["coverage"]),
        calibration=_parse_criterion("calibration", raw_dict["calibration"]),
        hallucination=_parse_criterion("hallucination", raw_dict["hallucination"]),
        tone_fit=_parse_criterion("tone_fit", raw_dict["tone_fit"]),
    )


def evaluate_corpus_gate(gate_outcomes: list[GateOutcome]) -> bool:
    """Corpus-majority gate (opt-in via ``--tuning-mode``).

    Blocks only if strictly more than 50% of narratives fail any hard-gate criterion.
    Strict D6 (any single failure blocks) is the default; this gate is reserved for
    iterative prompt-tuning sessions where a temporary widening keeps merges flowing
    while a regression is being driven down.
    """
    failures = sum(1 for g in gate_outcomes if not g.overall_passed)
    return failures > len(gate_outcomes) / 2


def evaluate_gate(audit_result: AuditResult) -> GateOutcome:
    """Deterministic gate evaluation per ADR-016 D6.

    Hard gates: faithfulness (score <= 2), coverage (fail), calibration (score <= 2),
    hallucination (fail).  tone_fit is warning-only.
    """
    hard_failures: list[str] = []
    warnings: list[str] = []

    for cr in [
        audit_result.faithfulness,
        audit_result.coverage,
        audit_result.calibration,
        audit_result.hallucination,
        audit_result.tone_fit,
    ]:
        if cr.criterion in _BLOCKING_CRITERIA and not cr.passed:
            hard_failures.append(cr.criterion)
        elif cr.criterion in _ADVISORY_CRITERIA and not cr.passed:
            warnings.append(cr.criterion)

    return GateOutcome(
        overall_passed=len(hard_failures) == 0,
        hard_gate_failures=len(hard_failures),
        advisory_failures=len(warnings),
        failure_criteria=hard_failures,
        warning_criteria=warnings,
    )


def calculate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    reasoning_tokens: int = 0,  # included for API parity; reasoning tokens already in completion_tokens
) -> float:
    """Calculate audit cost in USD for a single narrative call.

    GPT-5-mini pricing: $0.25/M input, $2.00/M output.
    Reasoning tokens are billed at the output rate and are already included in
    completion_tokens in the OpenAI API response — do not double-count.

    ADR-016 D1 cost formula:
        (prompt_tokens / 1_000_000 * 0.25) + (completion_tokens / 1_000_000 * 2.00)
    """
    input_cost = prompt_tokens / 1_000_000 * _PRICING["input_per_million"]
    output_cost = completion_tokens / 1_000_000 * _PRICING["output_per_million"]
    return input_cost + output_cost


def check_cost_ceiling(corpus_size: int, per_narrative_estimate_usd: float) -> None:
    """Raise ValueError if the estimated run cost exceeds AUDIT_MAX_COST_USD.

    Defaults to 0.50 if the env var is not set (10x headroom over ~$0.055/run).
    Raises before any API call is made (ADR-016 D8).
    """
    ceiling = float(os.environ.get("AUDIT_MAX_COST_USD", "0.50"))
    estimated = corpus_size * per_narrative_estimate_usd
    if estimated > ceiling:
        raise ValueError(
            f"Estimated run cost ${estimated:.4f} exceeds ceiling ${ceiling:.2f} "
            f"(AUDIT_MAX_COST_USD={ceiling}). "
            f"Corpus size: {corpus_size}, per-narrative estimate: ${per_narrative_estimate_usd:.4f}. "
            f"Raise AUDIT_MAX_COST_USD or reduce --limit to proceed."
        )


def generate_audit_run_id(
    source: str,
    sha: str | None = None,
    timestamp: int | None = None,
    date: str | None = None,
    hint: str | None = None,
) -> str:
    """Produce a structured audit_run_id string per ADR-016 D11 conventions.

    CI:      ci_<sha8>_<unix-timestamp>   e.g. ci_a1b2c3d4_1746316800
    Nightly: nightly_<YYYY-MM-DD>         e.g. nightly_2026-05-03
    Manual:  manual_<hint>_<unix-timestamp> e.g. manual_local_1746316800
    """
    if source == "ci":
        if sha is None:
            raise ValueError("sha is required for ci audit_run_id")
        ts = timestamp if timestamp is not None else int(time.time())
        return f"ci_{sha[:8]}_{ts}"
    elif source == "nightly":
        date_str = date if date is not None else datetime.now(UTC).strftime("%Y-%m-%d")
        return f"nightly_{date_str}"
    elif source == "manual":
        h = hint if hint is not None else "manual"
        ts = timestamp if timestamp is not None else int(time.time())
        return f"manual_{h}_{ts}"
    else:
        raise ValueError(f"Unknown audit source: {source!r}. Must be 'ci', 'nightly', or 'manual'.")


# ---------------------------------------------------------------------------
# DB helpers (thin wrappers over supabase client — patchable in tests)
# ---------------------------------------------------------------------------


def _insert_audit_row(table: str, row: dict) -> Any:
    """Insert a single row into the named table via supabase_client.

    Separated so tests can patch 'scripts.audit_narratives._insert_audit_row'
    without needing a real Supabase client.

    The supabase_client is passed via module-level state during a run.
    This function must only be called from within audit_one_narrative after
    _set_supabase_client() has been called.
    """
    if _supabase_client is None:
        raise RuntimeError("_insert_audit_row called before _set_supabase_client")
    _supabase_client.table(table).insert(row).execute()
    return None


def _delete_audit_rows_by_run_id(table: str, run_id: str, narrative_id: str) -> None:
    """Delete rows for a specific (narrative_id, audit_run_id) pair from the table.

    Used for compensating deletes when the aggregate row insert fails for one
    narrative — must NOT delete rows for other narratives in the same run that
    have already completed successfully. Scoped by both narrative_id and
    audit_run_id. Patchable in tests.
    """
    if _supabase_client is None:
        return
    (
        _supabase_client.table(table)
        .delete()
        .eq("audit_run_id", run_id)
        .eq("narrative_id", narrative_id)
        .execute()
    )


# Module-level supabase client slot — set before calling audit functions.
_supabase_client: Any = None


def _set_supabase_client(client: Any) -> None:
    global _supabase_client
    _supabase_client = client


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _body_excerpt(body: str, max_chars: int = 300) -> str:
    """Truncate body to max_chars, matching the generator's excerpt convention."""
    if len(body) <= max_chars:
        return body
    return body[:max_chars] + "..."


def _build_user_prompt(
    narrative: Any,
    signals: list[Any],
    dimension_configs: list[dict] | None = None,
    contacts: list[dict] | None = None,
    product_usage_config: dict | None = None,
    account_meta: dict | None = None,
) -> str:
    """Build the per-narrative user block for the audit prompt.

    narrative: Narrative domain object (or any object with matching attributes).
    signals: list of Signal domain objects (or _SignalRow wrappers).
    dimension_configs: list of {dimension_type, weight} dicts for enabled dimensions.
    contacts: list of {id, email, display_name, is_internal} dicts for the account
        roster. The auditor uses this to ground name claims (ADR-016 §D6 follow-up
        2026-05-06): a name in the roster is not a hallucination even if the current
        signal window does not reference that contact.
    product_usage_config: health_dimension_configs.config dict for the product_usage
        dimension (ADR-017 D5 cascade amendment). Passed to render_product_usage_trajectory
        so the auditor sees the same cascade tier selection as the narrative generator.
        None uses the default cascade [7, 14, 30, 60].
    account_meta: dict with account-level fields the generator sees: name, vertical,
        status, primary_domain, additional_domains. These are part of the allowable
        fact universe — a narrative that references the account's vertical or status
        is grounded, not hallucinating.
    """
    dims = dimension_configs or [
        {"dimension_type": "engagement", "weight": 0.5},
        {"dimension_type": "sentiment", "weight": 0.5},
    ]

    sentiment_val = getattr(narrative, "sentiment", None)
    engagement_val = getattr(narrative, "engagement", None)
    engagement_rationale = getattr(narrative, "engagement_rationale", "")

    lines = [
        f"Narrative ID: {narrative.id}",
        f"Account ID: {narrative.account_id}",
        "",
        f"Engagement score: {engagement_val} | Engagement rationale: {engagement_rationale}",
        f"Sentiment score: {sentiment_val} (null = not scored)",
        "",
        "Enabled dimensions:",
    ]
    for d in dims:
        lines.append(f"  - {d['dimension_type']} (weight: {d.get('weight', 'unknown')})")

    lines.append("")
    lines.append(f"Signal count: {len(signals)}")
    lines.append("")

    # VALID CONTACTS section — names here are part of the allowable fact universe.
    # See scripts/prompts/audit-narratives.md C1 (faithfulness) and C4 (hallucination).
    lines.append("--- VALID CONTACTS FOR THIS ACCOUNT ---")
    if contacts:
        roster = [c for c in contacts if not c.get("is_internal", False)]
        if roster:
            for c in roster:
                email = c.get("email", "")
                name = derive_display_name(c.get("display_name"), email)
                lines.append(f"- {name} <{email}>")
        else:
            lines.append("(no external contacts on roster)")
    else:
        lines.append("(contact roster not provided — evaluate names against signals only)")
    lines.append("")

    # VALID ACCOUNT METADATA section — these fields are part of the allowable fact
    # universe. The narrative generator receives vertical, status, primary_domain,
    # and additional_domains in its prompt context (see config/prompts/narrative.v1.md
    # §Account context). A narrative that references the account's vertical or status
    # is grounded, not hallucinating. See scripts/prompts/audit-narratives.md C4.
    lines.append("--- VALID ACCOUNT METADATA ---")
    if account_meta:
        lines.append(f"- name: {account_meta.get('name', 'unknown')}")
        lines.append(f"- vertical: {account_meta.get('vertical') or 'unspecified'}")
        lines.append(f"- status: {account_meta.get('status', 'unknown')}")
        lines.append(f"- primary_domain: {account_meta.get('primary_domain') or 'none'}")
        additional = account_meta.get("additional_domains") or []
        additional_str = ", ".join(additional) if additional else "none"
        lines.append(f"- additional_domains: {additional_str}")
    else:
        lines.append("(account metadata not provided — evaluate vertical/status references against signals only)")
    lines.append("")

    # Build a UUID -> contact dict so signal author_contact_id values can be
    # resolved to the actual email/display name for the auditor's view.
    contact_by_id: dict[str, dict] = {}
    if contacts:
        for c in contacts:
            cid = c.get("id")
            if cid is not None:
                contact_by_id[str(cid)] = c

    # Product usage trajectory block (ADR-017 D5 Option C).
    # The auditor sees the same deterministic context as the narrative LLM — any
    # trajectory claim in the narrative is verifiable against this block.
    lines.append("--- PRODUCT USAGE TRAJECTORY ---")
    try:
        from src.pipeline.product_usage_render import render_product_usage_trajectory

        signal_window_end = getattr(narrative, "signal_window_end", None)
        if signal_window_end is None:
            traj_now = datetime.now(UTC)
        elif isinstance(signal_window_end, datetime):
            traj_now = signal_window_end if signal_window_end.tzinfo else signal_window_end.replace(tzinfo=UTC)
        else:
            try:
                traj_now = datetime.fromisoformat(
                    str(signal_window_end).replace(" ", "T").replace("+00", "+00:00")
                )
            except ValueError:
                traj_now = datetime.now(UTC)

        traj_block = render_product_usage_trajectory(signals, traj_now, product_usage_config)
        lines.append(traj_block if traj_block else "(no product usage signals in window)")
    except ImportError:
        lines.append("(product usage render unavailable)")
    lines.append("")

    lines.append("--- NARRATIVE ---")
    lines.append(narrative.narrative)
    lines.append("")

    window_start = getattr(narrative, "signal_window_start", "unknown")
    window_end = getattr(narrative, "signal_window_end", "unknown")
    lines.append(
        f"--- SIGNALS CONSIDERED ({len(signals)} signals, window {window_start} to {window_end}) ---"
    )

    for sig in signals:
        sig_id = str(sig.id)
        occurred = getattr(sig, "occurred_at", "unknown")
        direction = getattr(sig, "direction", "")
        author_label = ""
        if hasattr(sig, "author_contact_id") and sig.author_contact_id:
            author_id = str(sig.author_contact_id)
            contact = contact_by_id.get(author_id)
            if contact:
                email = contact.get("email", "")
                name = derive_display_name(contact.get("display_name"), email)
                author_label = f"{name} <{email}>"
            else:
                author_label = author_id
        subject = getattr(sig, "subject", "") or ""
        body_raw = getattr(sig, "body", "") or ""
        excerpt = _body_excerpt(body_raw)
        lines.append(f"[{sig_id}] [{occurred}] [{direction}] [{author_label}] [{subject}]")
        lines.append(excerpt)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON schema for structured output (ADR-016 D1)
# ---------------------------------------------------------------------------

_AUDIT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {
            "type": "object",
            "properties": {
                "score": {"type": ["integer", "null"]},
                "passed": {"type": "boolean"},
                "reasoning": {"type": "string"},
                "details": {
                    "type": "object",
                    "properties": {"cited_signal_ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["cited_signal_ids"],
                    "additionalProperties": False,
                },
            },
            "required": ["score", "passed", "reasoning", "details"],
            "additionalProperties": False,
        },
        "coverage": {
            "type": "object",
            "properties": {
                "score": {"type": ["integer", "null"]},
                "passed": {"type": "boolean"},
                "reasoning": {"type": "string"},
                "details": {
                    "type": "object",
                    "properties": {"missing_dimensions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["missing_dimensions"],
                    "additionalProperties": False,
                },
            },
            "required": ["score", "passed", "reasoning", "details"],
            "additionalProperties": False,
        },
        "calibration": {
            "type": "object",
            "properties": {
                "score": {"type": ["integer", "null"]},
                "passed": {"type": "boolean"},
                "reasoning": {"type": "string"},
                # OpenAI strict mode requires every nested object to declare
                # `properties` + `required` + `additionalProperties:false`,
                # even when empty. Keep details as an empty closed object —
                # the reasoning string carries the explanation. Add named
                # properties here if calibration ever needs structured
                # side-channel data.
                "details": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            "required": ["score", "passed", "reasoning", "details"],
            "additionalProperties": False,
        },
        "hallucination": {
            "type": "object",
            "properties": {
                "score": {"type": ["integer", "null"]},
                "passed": {"type": "boolean"},
                "reasoning": {"type": "string"},
                "details": {
                    "type": "object",
                    "properties": {"invented_items": {"type": "array", "items": {"type": "string"}}},
                    "required": ["invented_items"],
                    "additionalProperties": False,
                },
            },
            "required": ["score", "passed", "reasoning", "details"],
            "additionalProperties": False,
        },
        "tone_fit": {
            "type": "object",
            "properties": {
                "score": {"type": ["integer", "null"]},
                "passed": {"type": "boolean"},
                "reasoning": {"type": "string"},
                # OpenAI strict mode — empty closed object, see calibration above.
                "details": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            "required": ["score", "passed", "reasoning", "details"],
            "additionalProperties": False,
        },
    },
    "required": ["faithfulness", "coverage", "calibration", "hallucination", "tone_fit"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Context fetcher — both CLI and simulator use this
# ---------------------------------------------------------------------------


def fetch_audit_context(
    narrative: Any,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    client: Any,
) -> AuditContext:
    """Fetch all auditor context from the DB in one place.

    Both the CLI main loop and the simulator's ``_audit_narrative`` call this
    so there is a single point of truth for what context the auditor receives.

    Args:
        narrative: Narrative domain object (or row wrapper) — used to look up
            ``signals_considered``.
        workspace_id: UUID of the workspace.
        account_id: UUID of the account.
        client: Supabase client (service-role).

    Returns:
        ``AuditContext`` with all 5 fields populated.  Empty lists/dicts are
        returned when the DB has no rows (e.g. an account with no contacts).
    """
    ws_id_str = str(workspace_id)
    acc_id_str = str(account_id)

    # 1. Signals
    signal_ids = list(getattr(narrative, "signals_considered", []) or [])
    if signal_ids:
        sig_ids_str = [str(s) for s in signal_ids]
        sigs_resp = (
            client.table("signals")
            .select("*")
            .in_("id", sig_ids_str)
            .eq("workspace_id", ws_id_str)
            .eq("account_id", acc_id_str)
            .execute()
        )
        signals: list[Any] = [_SignalRow(r) for r in (sigs_resp.data or [])]
    else:
        signals = []

    # 2. Contacts
    contacts_resp = (
        client.table("contacts")
        .select("id,email,display_name,is_internal")
        .eq("workspace_id", ws_id_str)
        .eq("account_id", acc_id_str)
        .is_("deleted_at", "null")
        .execute()
    )
    contacts: list[dict] = contacts_resp.data or []

    # 3. Account metadata
    acct_resp = (
        client.table("accounts")
        .select("name,vertical,status,primary_domain,additional_domains")
        .eq("id", acc_id_str)
        .eq("workspace_id", ws_id_str)
        .execute()
    )
    acct_rows = acct_resp.data or []
    account_meta: dict = acct_rows[0] if acct_rows else {}

    # 4. Dimension configs
    dim_resp = (
        client.table("health_dimension_configs")
        .select("dimension_type,weight")
        .eq("workspace_id", ws_id_str)
        .eq("enabled", True)
        .is_("deleted_at", "null")
        .execute()
    )
    dimension_configs: list[dict] = dim_resp.data or []

    # 5. Product usage config (cached per workspace in CLI; per-call here — acceptable
    #    cost since fetch_audit_context is called once per narrative)
    pu_resp = (
        client.table("health_dimension_configs")
        .select("config")
        .eq("workspace_id", ws_id_str)
        .eq("dimension_type", "product_usage")
        .eq("enabled", True)
        .is_("deleted_at", "null")
        .execute()
    )
    pu_rows = pu_resp.data or []
    product_usage_config: dict = pu_rows[0].get("config") or {} if pu_rows else {}

    # 6. Workspace slug (for audit-trail span attribute per SOC-2 Layer 1)
    ws_resp = (
        client.table("workspaces")
        .select("slug")
        .eq("id", ws_id_str)
        .single()
        .execute()
    )
    if not ws_resp.data:
        raise RuntimeError(
            f"Workspace lookup returned no data for workspace_id={ws_id_str}. "
            "Audit cannot proceed without a workspace_slug for the audit trail span."
        )
    workspace_slug: str = ws_resp.data["slug"]

    return AuditContext(
        signals=signals,
        contacts=contacts,
        account_meta=account_meta,
        dimension_configs=dimension_configs,
        product_usage_config=product_usage_config,
        workspace_slug=workspace_slug,
    )


# ---------------------------------------------------------------------------
# Core async audit function
# ---------------------------------------------------------------------------


def audit_one_narrative(
    narrative: Any,
    context: AuditContext,
    audit_run_id: str,
    audit_source: str,
    auditor_model: str = AUDITOR_MODEL,
    workspace_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> AuditResult:
    """Audit a single narrative: call GPT-5-mini, parse results, write DB rows.

    Writes 5 criterion rows to narrative_audits + 1 aggregate row to
    narrative_audit_runs.  Compensating delete on aggregate-row failure
    ensures no partial write state (ADR-016 D12).

    context: ``AuditContext`` carrying all per-narrative DB context (signals,
    contacts, account_meta, dimension_configs, product_usage_config).  All
    fields are required — use ``fetch_audit_context`` to populate it.

    dry_run=True skips all OpenAI and Supabase calls, returns a synthetic all-pass result.
    """
    if dry_run:
        return _make_dry_run_result()

    system_prompt = _PROMPT_PATH.read_text()
    user_prompt = _build_user_prompt(
        narrative, context.signals, context.dimension_configs,
        context.contacts,
        product_usage_config=context.product_usage_config,
        account_meta=context.account_meta,
    )

    # Construct OpenAI client lazily (not at module import time) so tests can patch
    # scripts.audit_narratives.openai.OpenAI before the client is created.
    client = openai.OpenAI()

    # Layer 1 audit evidence: tag the OTel span with workspace.slug so PostHog
    # LLM Analytics events are filterable by workspace even when content capture
    # is off (the default per SOC-2 control in src/observability/llm.py).
    # get_current_span() returns a no-op span when OTel is suppressed (e.g. in
    # pytest), so the is_recording() guard makes this branch free in tests.
    try:
        from opentelemetry import trace as _otel_trace

        _span = _otel_trace.get_current_span()
        if _span.is_recording():
            if context.workspace_slug and context.workspace_slug != "unknown":
                _span.set_attribute("workspace.slug", context.workspace_slug)
            _span.set_attribute("deploy_env", os.environ.get("DEPLOY_ENV", "development"))
    except Exception:
        pass

    response = client.chat.completions.create(
        model=auditor_model,
        # 16000 gives >5x headroom over the 3000 cap that caused a RuntimeError on
        # the revenant-systems narrative (lattice-build run 2026-05-11).  Verbose
        # low-effort reasoning occasionally blows through a 3000-token budget;
        # GPT-5-mini supports up to 16384 output tokens, so 16000 is a defensible
        # near-ceiling value that accommodates worst-case reasoning output.
        max_completion_tokens=16000,
        reasoning_effort="low",  # type: ignore[call-arg]
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "audit_result",
                "strict": True,
                "schema": _AUDIT_JSON_SCHEMA,
            },
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    usage = response.usage
    prompt_tokens = getattr(usage, "prompt_tokens", 0)
    completion_tokens = getattr(usage, "completion_tokens", 0)

    # Parse the structured response — prefer .parsed (structured-output path), fall back to .content
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise RuntimeError(
            f"Audit call hit max_completion_tokens limit ({finish_reason}); "
            "increase max_completion_tokens or reduce narrative length"
        )
    parsed = getattr(choice.message, "parsed", None)
    if parsed is not None:
        raw_dict = parsed
    else:
        content = choice.message.content or ""
        if not content:
            raise RuntimeError(
                f"Audit model returned empty content (finish_reason={finish_reason!r}). "
                "Check that the model supports structured outputs and that the schema is valid."
            )
        raw_dict = json.loads(content)

    audit_result = parse_audit_response(raw_dict)
    audit_result.prompt_tokens = prompt_tokens
    audit_result.completion_tokens = completion_tokens

    gate = evaluate_gate(audit_result)
    cost_per_criterion = calculate_cost(prompt_tokens, completion_tokens) / 5.0

    ws_id = str(workspace_id) if workspace_id is not None else str(narrative.workspace_id)
    narrative_id = str(narrative.id)
    now_iso = datetime.now(UTC).isoformat()

    criterion_row_ids: list[str] = []
    criteria_data = [
        audit_result.faithfulness,
        audit_result.coverage,
        audit_result.calibration,
        audit_result.hallucination,
        audit_result.tone_fit,
    ]

    # PostHog $ai_evaluation events — one per criterion. Surface the audit's
    # per-criterion verdicts in PostHog LLM Analytics, linked to the underlying
    # $ai_generation auto-emitted by the OpenAI OTel instrumentation. The trace
    # ID grabs the active OTel span (the OpenAI call's span, since we're inside
    # its context) so PostHog can correlate the events on the trace view.
    try:
        from opentelemetry import trace as _otel_trace_eval

        from src.analytics import track_ai_evaluation

        _eval_span = _otel_trace_eval.get_current_span()
        _eval_ctx = _eval_span.get_span_context() if _eval_span.is_recording() else None
        _trace_id_hex = format(_eval_ctx.trace_id, "032x") if _eval_ctx else None
        for _cr in criteria_data:
            track_ai_evaluation(
                workspace_id=ws_id,
                audit_run_id=audit_run_id,
                narrative_id=narrative_id,
                criterion=_cr.criterion,
                passed=_cr.passed,
                score=_cr.score,
                audit_source=audit_source,
                auditor_model=auditor_model,
                trace_id=_trace_id_hex,
            )
    except Exception:
        logger.warning("PostHog $ai_evaluation capture failed", exc_info=True)

    # Write 5 criterion rows
    for cr in criteria_data:
        # Size guard on the LLM-generated details JSONB. The schema permits
        # cited_signal_ids / invented_items arrays without explicit caps; a
        # pathological LLM response could produce very large payloads. Hard cap
        # at 64 KB serialized to keep the row size bounded and the table sane.
        details_size = len(json.dumps(cr.details))
        if details_size > 64_000:
            raise ValueError(
                f"audit details JSONB for criterion {cr.criterion!r} exceeds 64 KB "
                f"({details_size} bytes); refusing to write"
            )
        row_id = str(uuid.uuid4())
        criterion_row_ids.append(row_id)
        _insert_audit_row(
            "narrative_audits",
            {
                "id": row_id,
                "workspace_id": ws_id,
                "narrative_id": narrative_id,
                "audit_run_id": audit_run_id,
                "audit_source": audit_source,
                "criterion": cr.criterion,
                "passed": cr.passed,
                "score": cr.score,
                "reasoning": cr.reasoning,
                "details": cr.details,
                "auditor_model": auditor_model,
                "audited_at": now_iso,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_per_criterion,
            },
        )

    # Build aggregate score_summary
    score_summary = {
        "faithfulness": {"passed": audit_result.faithfulness.passed, "score": audit_result.faithfulness.score},
        "coverage": {"passed": audit_result.coverage.passed},
        "calibration": {"passed": audit_result.calibration.passed, "score": audit_result.calibration.score},
        "hallucination": {"passed": audit_result.hallucination.passed},
        "tone_fit": {"passed": audit_result.tone_fit.passed},
    }
    total_cost = calculate_cost(prompt_tokens, completion_tokens)

    # Write aggregate row — compensating delete on failure (ADR-016 D12)
    try:
        _insert_audit_row(
            "narrative_audit_runs",
            {
                "id": str(uuid.uuid4()),
                "workspace_id": ws_id,
                "narrative_id": narrative_id,
                "audit_run_id": audit_run_id,
                "overall_passed": gate.overall_passed,
                "hard_gate_failures": gate.hard_gate_failures,
                "advisory_failures": gate.advisory_failures,
                "score_summary": score_summary,
                "audit_source": audit_source,
                "auditor_model": auditor_model,
                "audited_at": now_iso,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": total_cost,
            },
        )
    except Exception as exc:
        # Compensating delete: roll back the 5 criterion rows for THIS narrative
        # only — other narratives in the same run that completed successfully
        # must keep their criterion rows.
        logger.error("aggregate row insert failed, rolling back criterion rows: %s", exc)
        _delete_audit_rows_by_run_id("narrative_audits", audit_run_id, narrative_id)
        raise

    return audit_result


def _make_dry_run_result() -> AuditResult:
    """Synthetic all-pass AuditResult for dry-run mode (no OpenAI call)."""
    return AuditResult(
        faithfulness=CriterionResult(
            criterion="faithfulness",
            passed=True,
            score=5,
            reasoning="[dry-run] synthetic pass",
            details={"cited_signal_ids": []},
        ),
        coverage=CriterionResult(
            criterion="coverage",
            passed=True,
            score=None,
            reasoning="[dry-run] synthetic pass",
            details={"missing_dimensions": []},
        ),
        calibration=CriterionResult(
            criterion="calibration",
            passed=True,
            score=5,
            reasoning="[dry-run] synthetic pass",
            details={},
        ),
        hallucination=CriterionResult(
            criterion="hallucination",
            passed=True,
            score=None,
            reasoning="[dry-run] synthetic pass",
            details={"invented_items": []},
        ),
        tone_fit=CriterionResult(
            criterion="tone_fit",
            passed=True,
            score=None,
            reasoning="[dry-run] synthetic pass",
            details={},
        ),
        prompt_tokens=0,
        completion_tokens=0,
    )


# ---------------------------------------------------------------------------
# Dry-run entry point (called directly from tests and --dry-run CLI flag)
# ---------------------------------------------------------------------------


def run_dry_run() -> AuditResult:
    """Exercise the full audit path without hitting OpenAI or Supabase.

    Returns a synthetic all-pass AuditResult.  Used by --dry-run CLI flag
    and by test_dry_run_mode in tests/test_audit_harness.py.
    """
    return _make_dry_run_result()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit account-intelligence narratives using GPT-5-mini as an independent cross-vendor evaluator."
    )
    p.add_argument("--narrative-id", help="UUID of a specific narrative to audit.")
    p.add_argument("--limit", type=int, default=None, help="Audit at most N narratives.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print results without calling OpenAI or writing to DB.",
    )
    p.add_argument(
        "--write-db",
        action="store_true",
        default=False,
        help="Persist results to Supabase. Required unless --dry-run is set.",
    )
    p.add_argument(
        "--audit-source",
        choices=["ci", "nightly", "manual"],
        default="manual",
        help="Audit source tag written to narrative_audits.audit_source.",
    )
    p.add_argument(
        "--sha",
        default=None,
        help="Commit SHA — required when --audit-source=ci. Used to build audit_run_id.",
    )
    p.add_argument(
        "--tuning-mode",
        action="store_true",
        default=False,
        help=(
            "Use the corpus-majority gate instead of strict D6 (ADR-016 §D6). "
            "Reserved for iterative prompt-tuning runs where a regression is being "
            "driven down — block only if strictly more than 50%% of narratives fail "
            "any hard-gate criterion. Off by default; default behaviour is strict."
        ),
    )
    p.add_argument(
        "--include-superseded",
        action="store_true",
        default=False,
        help=(
            "Include narratives that have been superseded (superseded_at IS NOT NULL). "
            "By default only active narratives (superseded_at IS NULL) are audited. "
            "Use with --narrative-id to re-audit a specific superseded narrative."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0 = pass, 1 = hard-gate failure, 2 = error)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # Require explicit mode: either --dry-run (no API/DB) or --write-db (real run).
    # Avoids the silent-dry-run trap where omitting both flags would skip all
    # work and return zero with no warning.
    if not args.dry_run and not args.write_db:
        parser.error("must specify exactly one of --dry-run or --write-db")
    if args.dry_run and args.write_db:
        parser.error("--dry-run and --write-db are mutually exclusive")

    # --narrative-id and --limit together would mislead the cost-ceiling check
    # (corpus_size collapses to 1 even when --limit specifies a larger query).
    if args.narrative_id and args.limit is not None:
        parser.error("--narrative-id and --limit are mutually exclusive")

    if args.dry_run:
        result = run_dry_run()
        gate = evaluate_gate(result)
        print(json.dumps({"dry_run": True, "overall_passed": gate.overall_passed}, indent=2))
        return 0

    # Cost ceiling check before any API calls
    corpus_size = 1 if args.narrative_id else (args.limit or _DEFAULT_CORPUS_SIZE_ESTIMATE)
    check_cost_ceiling(corpus_size, _PER_NARRATIVE_ESTIMATE_USD)

    audit_run_id = generate_audit_run_id(
        source=args.audit_source,
        sha=args.sha,
        hint="cli",
        timestamp=int(time.time()),
    )

    # Initialize LLM observability before the OpenAI client is constructed so the
    # instrumentor wraps the client constructor.  No-ops when POSTHOG_API_KEY is unset.
    try:
        from src.observability.llm import setup_llm_observability

        setup_llm_observability()
    except Exception:
        logger.warning("LLM observability setup failed — continuing without it", exc_info=True)

    # Real run requires Supabase and OpenAI clients.
    # Importing here keeps the module importable without all deps installed.
    from supabase import create_client  # type: ignore[import-untyped]

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    sb_client = create_client(supabase_url, supabase_key)
    _set_supabase_client(sb_client)

    # Fetch narratives
    query = sb_client.table("narratives").select("*")
    if not args.include_superseded:
        query = query.is_("superseded_at", "null")
    if args.narrative_id:
        query = query.eq("id", args.narrative_id)
    if args.limit:
        query = query.limit(args.limit)
    narratives_resp = query.execute()
    narratives_data = narratives_resp.data or []

    if not narratives_data:
        logger.warning("No narratives found — nothing to audit.")
        return 0

    gate_outcomes: list[GateOutcome] = []
    for n_row in narratives_data:
        narrative_id = n_row["id"]
        account_id_str = n_row.get("account_id")
        ws_id_str = n_row.get("workspace_id")

        narrative_obj = _NarrativeRow(n_row)

        if account_id_str and ws_id_str:
            audit_ctx = fetch_audit_context(
                narrative=narrative_obj,
                workspace_id=uuid.UUID(ws_id_str),
                account_id=uuid.UUID(account_id_str),
                client=sb_client,
            )
        else:
            # Narrative has no account/workspace IDs — degenerate case; provide
            # empty context so the auditor can still evaluate the narrative text.
            audit_ctx = AuditContext(
                signals=[],
                contacts=[],
                account_meta={},
                dimension_configs=[],
                product_usage_config={},
                workspace_slug="unknown",
            )

        try:
            result = audit_one_narrative(
                narrative=narrative_obj,
                context=audit_ctx,
                audit_run_id=audit_run_id,
                audit_source=args.audit_source,
                auditor_model=AUDITOR_MODEL,
                dry_run=False,
            )
        except Exception as exc:
            logger.error("audit failed for narrative %s: %s", narrative_id, exc)
            return 2

        gate = evaluate_gate(result)
        gate_outcomes.append(gate)
        status = "PASS" if gate.overall_passed else "FAIL"
        print(f"[{status}] narrative={narrative_id} hard_failures={gate.hard_gate_failures} warnings={gate.advisory_failures}")

    if args.tuning_mode:
        corpus_failed = evaluate_corpus_gate(gate_outcomes)
        failure_count = sum(1 for g in gate_outcomes if not g.overall_passed)
        print(
            f"[CORPUS GATE] {failure_count}/{len(gate_outcomes)} narratives failed "
            f"— {'BLOCK' if corpus_failed else 'PASS'} (threshold: >50% = >{len(gate_outcomes) / 2:.1f})"
        )
        return 1 if corpus_failed else 0
    else:
        return 1 if any(not g.overall_passed for g in gate_outcomes) else 0


class _NarrativeRow:
    """Thin wrapper for raw DB row dicts to expose attribute access."""

    def __init__(self, row: dict) -> None:
        self._row = row

    def __getattr__(self, name: str) -> Any:
        try:
            return self._row[name]
        except KeyError:
            raise AttributeError(name) from None


class _SignalRow:
    """Thin wrapper for raw DB row dicts to expose attribute access."""

    def __init__(self, row: dict) -> None:
        self._row = row

    def __getattr__(self, name: str) -> Any:
        try:
            return self._row[name]
        except KeyError:
            raise AttributeError(name) from None


if __name__ == "__main__":
    sys.exit(main())
