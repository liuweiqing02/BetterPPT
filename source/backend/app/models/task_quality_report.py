from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskQualityReport(Base):
    __tablename__ = 'task_quality_reports'
    __table_args__ = (UniqueConstraint('task_id', 'metric_version', name='uk_task_quality_task_metric'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(32), default='v1.0', nullable=False)
    evaluated_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pass_flag: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layout_offset_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    box_size_deviation_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    style_fidelity_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    text_slot_match_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    image_slot_match_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    table_slot_match_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    auto_fix_success_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    fallback_success_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    editable_text_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    locked_page_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    evaluated_scope_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)