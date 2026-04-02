from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TemplateSlotDefinition(Base):
    __tablename__ = 'template_slot_definitions'
    __table_args__ = (
        UniqueConstraint('template_profile_id', 'page_no', 'slot_key', name='uk_tpl_slot'),
        Index('idx_tpl_slot_type', 'template_profile_id', 'slot_type'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_profile_id: Mapped[int] = mapped_column(ForeignKey('template_profiles.id'), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_role: Mapped[str] = mapped_column(String(64), nullable=False)
    bbox_x: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    bbox_y: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    bbox_w: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    bbox_h: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    z_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    style_tokens_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    constraints_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)