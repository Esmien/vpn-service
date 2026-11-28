"""Public read contracts; connection material is not part of any response."""
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, IPvAnyAddress

Scenario = Literal['general', 'gemini', 'ru']
SCENARIOS = ('general', 'gemini', 'ru')
POOLS = (*SCENARIOS, 'lte')


class Pools(BaseModel):
    general: bool = False
    gemini: bool = False
    ru: bool = False
    lte: bool = False


class ProbeView(BaseModel):
    exit_id: str
    status: Literal['ok', 'bad', 'unknown']
    latency_ms: float | None
    checked_at: AwareDatetime


class NodeView(BaseModel):
    node_id: str
    original_name: str
    custom_name: str | None
    pools: Pools
    probes: dict[Scenario, ProbeView] = Field(default_factory=dict)  # Populated by the probe slice.


class SubscriptionView(BaseModel):
    sub_id: str
    version: int
    name: str
    tg_url: str | None
    fetch_status: Literal['active', 'error', 'needs_review']
    expires_at: AwareDatetime | None
    expiry_checked_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    error_snippet: str | None = None
    error_full: str | None = None
    nodes: list[NodeView]
    review: None = None  # Review intake is not enabled in the read-only slice.


class SubscriptionsView(BaseModel):
    subscriptions: list[SubscriptionView]


class OwnEgress(BaseModel):
    ip: IPvAnyAddress | None = None
    checked_at: AwareDatetime | None = None


class Health(BaseModel):
    state: Literal['unknown', 'primary_ok', 'fallback', 'temporary_own', 'degraded']
    reason: Literal['primary_failed', 'active_removed', 'probe_unconfirmed',
                    'fallbacks_exhausted', 'fallbacks_unconfirmed'] | None = None
    checked_at: AwareDatetime | None = None


class RouteView(BaseModel):
    policy_version: int
    own_exit_ids: list[str]
    primary: str
    fallbacks: list[str]
    active: str
    active_role: Literal['primary', 'fallback', 'temporary_own']
    health: Health


class RoutesView(BaseModel):
    own_names: dict[str, str]
    own_egress: dict[str, OwnEgress]
    scenarios: dict[Scenario, RouteView]
