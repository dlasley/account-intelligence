"""Pydantic schema for synthetic scenario YAML files.

Top-level version: 1. See ADR-015 §D3 for the full specification.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Mirrors the `Vertical` StrEnum and the `accounts_vertical_check` Postgres
# constraint. Update all three together when adding/removing a vertical.
VerticalLiteral = Literal[
    "software",
    "financial_services",
    "healthcare",
    "life_sciences",
    "education",
    "public_sector",
    "retail_consumer",
    "media_entertainment",
    "manufacturing",
    "energy_utilities",
    "professional_services",
    "nonprofit",
    "other",
]


class AxesSpec(BaseModel):
    """Eight variety axes that generators consult when producing signals.

    `extra="forbid"` ensures unrecognised axis names (e.g., a typo or a v1.5 axis added
    prematurely to the YAML) surface as a Pydantic ValidationError at load time rather
    than being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    contact_diversity: str = "single"  # "single" | "multi" | "crowded"
    contact_email_origin: str = "corporate"  # "corporate" | "personal_email" | "mixed"
    message_length: str = "paragraph"  # "short" | "paragraph" | "multi" | "chain"
    response_cadence: str = "steady"  # "burst" | "steady" | "drift" | "silence"
    # "formal" | "technical" | "casual" | "escalation" | "apologetic"
    email_tone: str = "technical"
    # "linear" | "branching" | "standalone" | "missing_thread_id"
    threading_topology: str = "linear"
    # "flat" | "declining" | "recovering" | "oscillating" | "sudden_escalation"
    sentiment_trajectory: str = "flat"
    # "email_heavy" | "product_heavy" | "balanced" | "divergent" | "aligned"
    cross_modal: str = "balanced"
    concern_topic: str = "none"
    # Values: "none" | "pricing" | "outage" | "feature_gap" | "utilization_decline"
    #         | "competitive" | "success_expansion" | "renewal_pending"
    # "none" (default) — no specific business concern in foreground; uses default templates.
    # All other values select a topically distinct template family (ADR-015 Rev 1).


class AccountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    primary_domain: str | None = None
    additional_domains: list[str] = []
    vertical: VerticalLiteral | None = None
    status: str = "active"  # "active" | "candidate" | "archived"


class TargetSpec(BaseModel):
    """Reserved for v1.5. Validated at load time; ignored by the orchestrator in v1."""

    model_config = ConfigDict(extra="forbid")

    outcome: str | None = None  # "renewed" | "churned" | "expanded"
    health_score: int | None = None  # expected overall_health_score [1-100]
    dimension_scores: list[dict] = []  # [{"dimension_id": str, "expected": int, "tolerance": int}]
    verification_method: str | None = None  # "label_only" | "score_assert" | "distribution_gate"


class SignalSpec(BaseModel):
    """Tagged-union by source_type per ADR-015 §D3. extra="forbid" surfaces
    YAML typos at load time rather than silently dropping them."""

    model_config = ConfigDict(extra="forbid")

    source_type: str  # must be a valid SourceType value
    account_slug: str  # must match an entry in accounts or a quantas-labs-shaped account
    count: int = 1
    axes: AxesSpec = AxesSpec()
    overrides: dict = {}  # field-level overrides applied after generation


class ExpectedSignalSpec(BaseModel):
    """Per-account routing expectations for equivalence assertions.
    Populated by the YAML author from the derive_quantas_labs_baseline.py script;
    read by tests/synthetic/test_quantas_labs_equivalence.py.
    """

    model_config = ConfigDict(extra="forbid")

    account_slug: str
    signal_count: int
    routing_distribution: dict[str, int]
    sender_domains: list[str]


class ScenarioSpec(BaseModel):
    """Top-level scenario per ADR-015 §D3. extra="forbid" so typos at the top
    level (e.g., 'wrokspace_slug') surface as ValidationError, not a silent default."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str
    seed: int
    description: str = ""
    workspace_slug: str = "quantas-labs"  # reuse quantas-labs workspace unless overridden
    accounts: list[AccountSpec] = []  # empty = reuse quantas-labs-shaped accounts/
    signals: list[SignalSpec]
    target: TargetSpec | None = None  # reserved; ignored by orchestrator in v1
    expected_routing: list[ExpectedSignalSpec] = []  # equivalence anchors; ignored by orchestrator
