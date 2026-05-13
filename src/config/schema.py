from pydantic import BaseModel


class EngagementTierConfig(BaseModel):
    name: str
    score: int
    min_signals: int
    window_days: int
    min_contacts: int


class SentimentBandConfig(BaseModel):
    min_score: int
    label: str


class AccountHealthConfig(BaseModel):
    engagement_tiers: list[EngagementTierConfig]
    sentiment_bands: list[SentimentBandConfig]


class DimensionScoringConfig(BaseModel):
    dimension_type: str
    name: str
    weight: float
    enabled: bool
    config: dict = {}


class HealthScoringConfig(BaseModel):
    formula: str = "weighted_average"
    dimensions: list[DimensionScoringConfig]


class NarrativeConfig(BaseModel):
    model: str
    max_signals_in_context: int
    prompt_template_path: str
    include_vertical_hint: bool


class RoutingConfig(BaseModel):
    personal_provider_domains: list[str]


class OutreachConfig(BaseModel):
    max_signals_in_context: int
    templates_path: str


class ApiConfig(BaseModel):
    ingest_rate_limit_per_minute: int = 600
    rate_limit_per_minute: int = 100


class IntegrationsConfig(BaseModel):
    # Number of consecutive poll failures before a credential is deactivated.
    # At 15-minute cadence, 5 failures ≈ 75 minutes of outage before auto-disable.
    max_consecutive_errors: int = 5


class Config(BaseModel):
    inbound_domain: str
    account_health: AccountHealthConfig
    health_scoring: HealthScoringConfig
    narrative_generation: NarrativeConfig
    routing: RoutingConfig
    outreach_generation: OutreachConfig
    api: ApiConfig = ApiConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()
