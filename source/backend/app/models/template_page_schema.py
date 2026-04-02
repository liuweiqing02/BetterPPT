from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TemplatePageSchema(Base):
    __tablename__ = 'template_page_schemas'
    __table_args__ = (UniqueConstraint('template_profile_id', 'page_no', name='uk_tpl_schema_page'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_profile_id: Mapped[int] = mapped_column(ForeignKey('template_profiles.id'), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_label: Mapped[str] = mapped_column(String(64), nullable=False)
    page_function: Mapped[str] = mapped_column(String(64), nullable=False)
    layout_schema_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    style_tokens_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
