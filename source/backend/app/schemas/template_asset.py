from datetime import datetime
from decimal import Decimal
from typing import Any

from app.schemas.common import BaseSchema


class TemplateAssetProfileData(BaseSchema):
    id: int
    file_id: int
    profile_version: str
    total_pages: int
    cluster_count: int
    embedding_model: str
    llm_model: str
    summary_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class TemplateAssetPageData(BaseSchema):
    page_no: int
    cluster_label: str
    page_function: str
    layout_schema_json: dict[str, Any]
    style_tokens_json: dict[str, Any] | None = None


class TemplateAssetSlotData(BaseSchema):
    page_no: int
    slot_key: str
    slot_type: str
    slot_role: str
    bbox_x: Decimal
    bbox_y: Decimal
    bbox_w: Decimal
    bbox_h: Decimal
    z_index: int
    style_tokens_json: dict[str, Any] | None = None
    constraints_json: dict[str, Any] | None = None


class TemplateAssetsData(BaseSchema):
    file_id: int
    profile: TemplateAssetProfileData
    pages: list[TemplateAssetPageData]
    slots: list[TemplateAssetSlotData]
    asset_pages_count: int
    slots_count: int
