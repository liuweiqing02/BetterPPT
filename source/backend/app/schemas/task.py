from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class TaskCreateRequest(BaseModel):
    source_file_id: int
    reference_file_id: int
    detail_level: str
    user_prompt: str | None = None
    rag_enabled: bool = False
    idempotency_key: str | None = None


class TaskCreateData(BaseSchema):
    task_no: str
    status: str


class TaskSummary(BaseSchema):
    task_no: str
    status: str
    current_step: str | None = None
    progress: int
    detail_level: str
    page_count_estimated: int | None = None
    page_count_final: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListData(BaseSchema):
    items: list[TaskSummary]


class TaskEventData(BaseSchema):
    id: int
    event_type: str
    event_time: datetime
    message: str | None = None
    payload_json: dict | None = None


class TaskEventsListData(BaseSchema):
    items: list[TaskEventData]
    next_cursor: int | None = None


class TaskReplayStepData(BaseSchema):
    id: int
    step_order: int
    step_code: str
    step_status: str
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    input_json: dict | None = None
    output_json: dict | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskReplayData(BaseSchema):
    task: TaskSummary
    steps: list[TaskReplayStepData]
    events: list[TaskEventData]
    next_cursor: int | None = None


class TaskSlotFillingSummaryData(BaseSchema):
    slot_key: str
    fill_status: str


class TaskMappingItemData(BaseSchema):
    slide_no: int
    template_page_no: int
    fallback_level: int
    slot_fillings: list[TaskSlotFillingSummaryData] = []


class TaskMappingsData(BaseSchema):
    task_no: str
    attempt_no: int | None = None
    items: list[TaskMappingItemData]
    next_cursor: str | None = None


class TaskQualityReportData(BaseSchema):
    task_no: str
    metric_version: str | None = None
    evaluated_pages: int | None = None
    pass_flag: int | None = None
    layout_offset_ratio: float | None = None
    box_size_deviation_ratio: float | None = None
    style_fidelity_score: float | None = None
    text_slot_match_rate: float | None = None
    image_slot_match_rate: float | None = None
    table_slot_match_rate: float | None = None
    auto_fix_success_rate: float | None = None
    fallback_success_rate: float | None = None
    editable_text_ratio: float | None = None
    locked_page_ratio: float | None = None
    evaluated_scope_json: dict[str, Any] | None = None
    report_json: dict[str, Any] | None = None


class PreviewSlide(BaseSchema):
    slide_no: int
    page_no: int
    image_url: str
    width: int = 1920
    height: int = 1080
    file_id: int | None = None
    storage_path: str | None = None
    mime_type: str | None = None


class TaskPreviewData(BaseSchema):
    task_no: str
    slides: list[PreviewSlide]
    expires_in: int = 3600
    preview_source: str = 'file_url'
    preview_manifest: dict[str, Any] | None = None


class TaskResultData(BaseSchema):
    file_id: int
    filename: str
    download_url: str
    expires_in: int = 3600


class TaskActionResponse(BaseSchema):
    task_no: str
    status: str
    message: str = Field(default='ok')


class TaskDeleteData(BaseSchema):
    task_no: str
    status: str
    message: str = Field(default='ok')
    deleted_at: datetime | None = None
    cleaned_file_count: int = 0
