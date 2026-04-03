from enum import StrEnum


class FileRole(StrEnum):
    PDF_SOURCE = 'pdf_source'
    PPT_REFERENCE = 'ppt_reference'
    PPT_RESULT = 'ppt_result'
    ASSET_IMAGE = 'asset_image'


class TaskStatus(StrEnum):
    CREATED = 'created'
    VALIDATING = 'validating'
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELED = 'canceled'


class TaskStepCode(StrEnum):
    VALIDATE_INPUT = 'validate_input'
    PARSE_PDF = 'parse_pdf'
    ANALYZE_TEMPLATE = 'analyze_template'
    ASSETIZE_TEMPLATE = 'assetize_template'
    RAG_RETRIEVE = 'rag_retrieve'
    PLAN_SLIDES = 'plan_slides'
    MAP_SLOTS = 'map_slots'
    GENERATE_SLIDES = 'generate_slides'
    SELF_CORRECT = 'self_correct'
    EXPORT_PPT = 'export_ppt'


class TaskStepStatus(StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    SKIPPED = 'skipped'


class TaskEventType(StrEnum):
    STATUS_CHANGED = 'status_changed'
    PROGRESS_UPDATED = 'progress_updated'
    STEP_LOG = 'step_log'
    WARNING = 'warning'
    ERROR = 'error'
    FALLBACK_STARTED = 'fallback_started'
    FALLBACK_FINISHED = 'fallback_finished'
    FALLBACK_FAILED = 'fallback_failed'


DETAIL_LEVEL_PAGE_RANGE: dict[str, tuple[int, int]] = {
    'concise': (8, 12),
    'balanced': (13, 20),
    'detailed': (21, 30),
}

ALLOWED_DETAIL_LEVELS = set(DETAIL_LEVEL_PAGE_RANGE.keys())

STEP_PROGRESS_RANGE: dict[str, tuple[int, int]] = {
    TaskStepCode.VALIDATE_INPUT: (0, 5),
    TaskStepCode.PARSE_PDF: (6, 15),
    TaskStepCode.ANALYZE_TEMPLATE: (16, 25),
    TaskStepCode.ASSETIZE_TEMPLATE: (26, 40),
    TaskStepCode.RAG_RETRIEVE: (41, 48),
    TaskStepCode.PLAN_SLIDES: (49, 60),
    TaskStepCode.MAP_SLOTS: (61, 72),
    TaskStepCode.GENERATE_SLIDES: (73, 88),
    TaskStepCode.SELF_CORRECT: (89, 96),
    TaskStepCode.EXPORT_PPT: (97, 100),
}
