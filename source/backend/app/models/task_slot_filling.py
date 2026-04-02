from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskSlotFilling(Base):
    __tablename__ = 'task_slot_fillings'
    __table_args__ = (
        UniqueConstraint('task_id', 'attempt_no', 'slide_no', 'slot_key', name='uk_task_slot_fill_attempt'),
        Index('idx_task_slot_task_slide', 'task_id', 'attempt_no', 'slide_no'),
        Index('idx_task_slot_status', 'task_id', 'fill_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    slide_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_source: Mapped[str] = mapped_column(String(32), nullable=False)
    fill_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    overflow_flag: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    overlap_flag: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    fill_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)