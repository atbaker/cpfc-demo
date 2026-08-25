from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

Engine = Literal["naive", "temporal"]
OrderSource = Literal["audience", "synthetic"]


@dataclass(frozen=True)
class OrderRequest:
    order_id: str
    run_id: str
    sequence_number: int
    source: OrderSource
    supporter_alias: str
    section: str
    price_pence: int
    engine: Engine


class CreateOrderPayload(BaseModel):
    join_code: str
    supporter_alias: str = Field(default="", max_length=40)
    section: str = Field(default="Holmesdale Road", max_length=40)


class ServiceRequest(BaseModel):
    order: OrderRequest
    attempt: int = Field(ge=1, le=1000)


class ServiceResponse(BaseModel):
    resource_id: str
    duplicate: bool = False
    crash_after_commit: bool = False
    message: str = "ok"


class FaultUpdate(BaseModel):
    reservation_failure_pct: int = Field(default=0, ge=0, le=100)
    payment_failure_pct: int = Field(default=0, ge=0, le=100)
    ticket_failure_pct: int = Field(default=0, ge=0, le=100)
    card_decline_pct: int = Field(default=0, ge=0, le=100)
    latency_ms: int = Field(default=180, ge=0, le=5000)


class GeneratorStart(BaseModel):
    rate_per_second: float = Field(default=25, ge=0.5, le=30)
    target_count: int = Field(default=500, ge=1, le=1000)


class CrashArm(BaseModel):
    target_source: Literal["audience", "any"] = "audience"
    target_order_id: str | None = None


class StatusUpdate(BaseModel):
    status: Literal["processing", "failed", "stranded", "complete"]
    message: str = Field(default="", max_length=200)
