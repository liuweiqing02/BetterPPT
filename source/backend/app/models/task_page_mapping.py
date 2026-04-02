from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskPageMapping(Base):
    __tablename__ = 'task_page_mappings'
    __table_args__ = (
        UniqueConstraint('task_id', 'attempt_no', 'slide_no', name='uk_task_page_map_attempt'),
        Index('idx_task_page_map_task_attempt', 'task_id', 'attempt_no'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    slide_no: Mapped[int] = mapped_column(Integer, nullable=False)
    page_function: Mapped[str] = mapped_column(String(64), nullable=False)
    template_page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    fallback_level: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)