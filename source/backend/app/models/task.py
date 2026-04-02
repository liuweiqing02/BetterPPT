from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (
        UniqueConstraint('task_no', name='uk_tasks_task_no'),
        UniqueConstraint('user_id', 'idempotency_key', name='uk_tasks_idempotency'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    task_no: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_id: Mapped[int] = mapped_column(ForeignKey('files.id'), nullable=False)
    reference_file_id: Mapped[int] = mapped_column(ForeignKey('files.id'), nullable=False)
    template_profile_id: Mapped[int | None] = mapped_column(ForeignKey('template_profiles.id'), nullable=True)
    result_file_id: Mapped[int | None] = mapped_column(ForeignKey('files.id'), nullable=True)
    detail_level: Mapped[str] = mapped_column(String(16), nullable=False)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    rag_enabled: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count_estimated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    fallback_used: Mapped[int] = mapped_column(default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)