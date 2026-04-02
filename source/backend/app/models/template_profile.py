from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TemplateProfile(Base):
    __tablename__ = 'template_profiles'
    __table_args__ = (UniqueConstraint('file_id', 'profile_version', name='uk_template_profiles_file_ver'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(ForeignKey('files.id'), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(32), default='v1', nullable=False)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(64), default='vit-base', nullable=False)
    llm_model: Mapped[str] = mapped_column(String(64), default='gpt-4.1-mini', nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
