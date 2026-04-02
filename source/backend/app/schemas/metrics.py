from typing import Any

from app.schemas.common import BaseSchema


class MetricsErrorCodeItem(BaseSchema):
    error_code: str
    count: int


class MetricsQualityFlagItem(BaseSchema):
    signal: str
    count: int


class MetricsOverviewData(BaseSchema):
    total_tasks: int
    success_tasks: int
    failed_tasks: int
    canceled_tasks: int
    success_rate: float
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    error_code_top: list[MetricsErrorCodeItem]
    self_correct_coverage: float
    avg_quality_risk: float
    high_risk_tasks: int
    quality_flags_top: list[MetricsQualityFlagItem]
