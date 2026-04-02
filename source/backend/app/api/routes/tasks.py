from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.constants import FileRole, TaskStatus
from app.core.errors import AppException
from app.core.security import CurrentUser
from app.db.session import get_db
from app.models.file import File
from app.models.task import Task
from app.schemas.common import APIResponse
from app.schemas.task import (
    PreviewSlide,
    TaskActionResponse,
    TaskCreateData,
    TaskCreateRequest,
    TaskDeleteData,
    TaskEventsListData,
    TaskEventData,
    TaskMappingItemData,
    TaskMappingsData,
    TaskListData,
    TaskPreviewData,
    TaskQualityReportData,
    TaskReplayData,
    TaskReplayStepData,
    TaskResultData,
    TaskSummary,
)
from app.services.file_service import get_file_by_id
from app.services.task_service import (
    cancel_task,
    create_task,
    get_task_by_no,
    get_task_quality_report,
    get_task_replay,
    list_task_events,
    list_task_page_mappings,
    list_tasks,
    delete_task,
    retry_task,
)

router = APIRouter(prefix='/tasks', tags=['tasks'])


def _task_summary(task) -> TaskSummary:
    return TaskSummary(
        task_no=task.task_no,
        status=task.status,
        current_step=task.current_step,
        progress=task.progress,
        detail_level=task.detail_level,
        page_count_estimated=task.page_count_estimated,
        page_count_final=task.page_count_final,
        error_code=task.error_code,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _build_data_url_preview(task_no: str, page_count: int) -> list[PreviewSlide]:
    placeholder_image = (
        'data:image/svg+xml;utf8,'
        "<svg xmlns='http://www.w3.org/2000/svg' width='1920' height='1080'>"
        "<rect width='100%' height='100%' fill='%23f4f7fb'/>"
        "<text x='100' y='160' font-size='56' fill='%23232f3e'>BetterPPT Preview</text>"
        "<text x='100' y='250' font-size='38' fill='%23445566'>Task "
        + task_no
        + "</text></svg>"
    )
    count = max(3, page_count or 3)
    return [PreviewSlide(slide_no=i, page_no=i, image_url=placeholder_image) for i in range(1, count + 1)]


def _build_file_url_preview(task: Task, db: Session, request: Request) -> list[PreviewSlide]:
    settings = get_settings()
    prefix = f'{settings.result_subdir}/{task.user_id}/{task.task_no}/preview/'
    files = list(
        db.scalars(
            select(File)
            .where(
                File.user_id == task.user_id,
                File.file_role == FileRole.ASSET_IMAGE,
                File.storage_path.like(f'{prefix}%'),
            )
            .order_by(File.filename.asc())
        ).all()
    )
    base_url = str(request.base_url).rstrip('/')
    slides: list[PreviewSlide] = []
    for file in files:
        slides.append(
            PreviewSlide(
                slide_no=_extract_page_no(file.filename),
                page_no=_extract_page_no(file.filename),
                image_url=f'{base_url}/api/v1/files/download/{file.id}',
                file_id=file.id,
                storage_path=file.storage_path,
                mime_type=file.mime_type,
            )
        )
    slides.sort(key=lambda item: item.slide_no)
    return slides


def _extract_page_no(filename: str) -> int:
    stem = filename.rsplit('.', 1)[0]
    parts = stem.split('_')
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    return 0


@router.post('', response_model=APIResponse)
def post_create_task(
    payload: TaskCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = create_task(
        db,
        user_id=current_user.id,
        source_file_id=payload.source_file_id,
        reference_file_id=payload.reference_file_id,
        detail_level=payload.detail_level,
        user_prompt=payload.user_prompt,
        rag_enabled=payload.rag_enabled,
        idempotency_key=payload.idempotency_key,
    )
    return APIResponse(data=TaskCreateData(task_no=task.task_no, status=task.status))


@router.get('', response_model=APIResponse)
def get_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    tasks = list_tasks(db, user_id=current_user.id, limit=limit)
    items = [_task_summary(task) for task in tasks]
    return APIResponse(data=TaskListData(items=items))


@router.get('/{task_no}', response_model=APIResponse)
def get_task_detail(
    task_no: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    return APIResponse(data=_task_summary(task))


@router.get('/{task_no}/events', response_model=APIResponse)
def get_task_events(
    task_no: str,
    cursor: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    items, next_cursor = list_task_events(db, task_id=task.id, cursor=cursor, limit=limit)
    data = TaskEventsListData(
        items=[TaskEventData.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )
    return APIResponse(data=data)


@router.get('/{task_no}/replay', response_model=APIResponse)
def get_task_replay_view(
    task_no: str,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task, steps, events, next_cursor = get_task_replay(db, user_id=current_user.id, task_no=task_no, limit=limit)
    data = TaskReplayData(
        task=_task_summary(task),
        steps=[TaskReplayStepData.model_validate(step) for step in steps],
        events=[TaskEventData.model_validate(event) for event in events],
        next_cursor=next_cursor,
    )
    return APIResponse(data=data)


@router.get('/{task_no}/quality-report', response_model=APIResponse)
def get_task_quality_report_view(
    task_no: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    report = get_task_quality_report(db, task_id=task.id)
    data = TaskQualityReportData(task_no=task.task_no)
    if report:
        data.metric_version = report.metric_version
        data.evaluated_pages = report.evaluated_pages
        data.pass_flag = report.pass_flag
        data.layout_offset_ratio = float(report.layout_offset_ratio) if report.layout_offset_ratio is not None else None
        data.box_size_deviation_ratio = (
            float(report.box_size_deviation_ratio) if report.box_size_deviation_ratio is not None else None
        )
        data.style_fidelity_score = float(report.style_fidelity_score) if report.style_fidelity_score is not None else None
        data.text_slot_match_rate = float(report.text_slot_match_rate) if report.text_slot_match_rate is not None else None
        data.image_slot_match_rate = float(report.image_slot_match_rate) if report.image_slot_match_rate is not None else None
        data.table_slot_match_rate = float(report.table_slot_match_rate) if report.table_slot_match_rate is not None else None
        data.auto_fix_success_rate = float(report.auto_fix_success_rate) if report.auto_fix_success_rate is not None else None
        data.fallback_success_rate = float(report.fallback_success_rate) if report.fallback_success_rate is not None else None
        data.editable_text_ratio = float(report.editable_text_ratio) if report.editable_text_ratio is not None else None
        data.locked_page_ratio = float(report.locked_page_ratio) if report.locked_page_ratio is not None else None
        data.evaluated_scope_json = report.evaluated_scope_json
        data.report_json = report.report_json
    return APIResponse(data=data)


@router.get('/{task_no}/mappings', response_model=APIResponse)
def get_task_mappings_view(
    task_no: str,
    attempt_no: str = Query(default='latest'),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    items, resolved_attempt_no, next_cursor = list_task_page_mappings(
        db,
        task_id=task.id,
        attempt_no=attempt_no,
        cursor=cursor,
        limit=limit,
    )
    data = TaskMappingsData(
        task_no=task.task_no,
        attempt_no=resolved_attempt_no,
        items=[TaskMappingItemData.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )
    return APIResponse(data=data)


@router.post('/{task_no}/retry', response_model=APIResponse)
def post_retry_task(
    task_no: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    task = retry_task(db, task=task)
    return APIResponse(data=TaskActionResponse(task_no=task.task_no, status=task.status, message='task retried'))


@router.post('/{task_no}/cancel', response_model=APIResponse)
def post_cancel_task(
    task_no: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    task = cancel_task(db, task=task)
    return APIResponse(data=TaskActionResponse(task_no=task.task_no, status=task.status, message='task canceled'))


@router.post('/{task_no}/delete', response_model=APIResponse)
def post_delete_task(
    task_no: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    data = delete_task(db, user_id=current_user.id, task_no=task_no)
    return APIResponse(data=TaskDeleteData.model_validate(data))


@router.get('/{task_no}/preview', response_model=APIResponse)
def get_task_preview(
    task_no: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    if task.status != TaskStatus.SUCCEEDED:
        raise AppException(status_code=409, code=1004, message='task is not succeeded yet')

    file_slides = _build_file_url_preview(task, db, request)
    if file_slides:
        data = TaskPreviewData(
            task_no=task.task_no,
            slides=file_slides,
            expires_in=3600,
            preview_source='file_url',
            preview_manifest={
                'count': len(file_slides),
                'file_ids': [slide.file_id for slide in file_slides if slide.file_id is not None],
            },
        )
        return APIResponse(data=data)

    fallback_slides = _build_data_url_preview(task.task_no, task.page_count_final or task.page_count_estimated or 3)
    data = TaskPreviewData(
        task_no=task.task_no,
        slides=fallback_slides,
        expires_in=3600,
        preview_source='data_url',
        preview_manifest=None,
    )
    return APIResponse(data=data)


@router.get('/{task_no}/result', response_model=APIResponse)
def get_task_result(
    task_no: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    task = get_task_by_no(db, user_id=current_user.id, task_no=task_no)
    if task.status != TaskStatus.SUCCEEDED or not task.result_file_id:
        raise AppException(status_code=409, code=1004, message='task result is not ready')

    result_file = get_file_by_id(db, task.result_file_id)
    if not result_file:
        raise AppException(status_code=404, code=1002, message='result file missing')

    base_url = str(request.base_url).rstrip('/')
    download_url = f'{base_url}/api/v1/files/download/{result_file.id}'
    data = TaskResultData(
        file_id=result_file.id,
        filename=result_file.filename,
        download_url=download_url,
        expires_in=3600,
    )
    return APIResponse(data=data)
