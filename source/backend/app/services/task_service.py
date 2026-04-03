from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from random import randint
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import asc, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import (
    ALLOWED_DETAIL_LEVELS,
    DETAIL_LEVEL_PAGE_RANGE,
    FileRole,
    TaskEventType,
    TaskStatus,
)
from app.core.errors import AppException
from app.models.file import File
from app.models.task_page_mapping import TaskPageMapping
from app.models.task_quality_report import TaskQualityReport
from app.models.task_slot_filling import TaskSlotFilling
from app.models.task import Task
from app.models.task_event import TaskEvent
from app.models.task_step import TaskStep
from app.services.event_service import add_task_event
from app.services.file_service import delete_file
from app.services.queue_service import cache_task_progress, enqueue_task, get_redis_client, push_task_event_cache


def generate_task_no() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime('T%Y%m%d%H%M%S') + f'{randint(1000, 9999)}'


def _validate_task_files(
    db: Session,
    *,
    user_id: int,
    source_file_id: int,
    reference_file_id: int,
) -> tuple[File, File]:
    source = db.get(File, source_file_id)
    reference = db.get(File, reference_file_id)

    if not source or source.user_id != user_id:
        raise AppException(status_code=404, code=1002, message='source file not found')
    if not reference or reference.user_id != user_id:
        raise AppException(status_code=404, code=1002, message='reference file not found')
    if source.file_role != FileRole.PDF_SOURCE:
        raise AppException(status_code=400, code=1001, message='source file must be pdf_source')
    if reference.file_role != FileRole.PPT_REFERENCE:
        raise AppException(status_code=400, code=1001, message='reference file must be ppt_reference')
    if source.status != 'uploaded' or reference.status != 'uploaded':
        raise AppException(status_code=400, code=1001, message='files must be uploaded before creating task')
    if source.scan_status != 'clean':
        raise AppException(
            status_code=400,
            code=1001,
            message='source file must be clean',
            data={
                'file_id': source.id,
                'scan_status': source.scan_status,
                'scan_report_json': source.scan_report_json or {},
            },
        )
    if reference.scan_status != 'clean':
        raise AppException(
            status_code=400,
            code=1001,
            message='reference file must be clean',
            data={
                'file_id': reference.id,
                'scan_status': reference.scan_status,
                'scan_report_json': reference.scan_report_json or {},
            },
        )

    return source, reference


def _estimate_page_count(detail_level: str) -> int:
    low, high = DETAIL_LEVEL_PAGE_RANGE[detail_level]
    return (low + high) // 2


def _check_create_task_rate_limit(user_id: int) -> None:
    settings = get_settings()
    limit = int(settings.rate_limit_create_task_per_minute or 0)
    if limit <= 0:
        return

    client = get_redis_client()
    if client is None:
        return

    key = f'rate_limit:create_task:{int(user_id)}'
    try:
        current = int(client.incr(key))
        if current == 1:
            client.expire(key, 60)
    except RedisError:
        return

    if current > limit:
        ttl = 60
        try:
            ttl = max(1, int(client.ttl(key)))
        except Exception:
            ttl = 60
        raise AppException(
            status_code=429,
            code=1004,
            message='create task rate limited',
            data={'limit_per_minute': limit, 'retry_after_seconds': ttl},
        )


def _check_create_task_concurrency_limit(db: Session, user_id: int) -> None:
    settings = get_settings()
    limit = int(settings.task_concurrency_per_user or 0)
    if limit <= 0:
        return
    active_window_minutes = max(1, int(settings.task_concurrency_active_window_minutes or 120))
    active_cutoff = datetime.utcnow() - timedelta(minutes=active_window_minutes)
    running_like = {
        TaskStatus.CREATED,
        TaskStatus.VALIDATING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    }
    current = int(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.user_id == user_id,
                Task.status.in_(running_like),
                Task.updated_at >= active_cutoff,
            )
        )
        or 0
    )
    if current >= limit:
        raise AppException(
            status_code=429,
            code=1004,
            message='task concurrency limit reached',
            data={'limit': limit, 'current': current},
        )


def create_task(
    db: Session,
    *,
    user_id: int,
    source_file_id: int,
    reference_file_id: int,
    detail_level: str,
    user_prompt: str | None,
    rag_enabled: bool,
    idempotency_key: str | None,
) -> Task:
    detail_level = detail_level.strip().lower()
    if detail_level not in ALLOWED_DETAIL_LEVELS:
        raise AppException(status_code=400, code=1001, message='invalid detail_level')

    _check_create_task_rate_limit(user_id)
    _check_create_task_concurrency_limit(db, user_id)

    _validate_task_files(
        db,
        user_id=user_id,
        source_file_id=source_file_id,
        reference_file_id=reference_file_id,
    )

    if idempotency_key:
        existing = db.scalar(
            select(Task).where(Task.user_id == user_id, Task.idempotency_key == idempotency_key)
        )
        if existing:
            raise AppException(
                status_code=409,
                code=1005,
                message='idempotency conflict',
                data={'task_no': existing.task_no, 'status': existing.status},
            )

    task = Task(
        user_id=user_id,
        task_no=generate_task_no(),
        source_file_id=source_file_id,
        reference_file_id=reference_file_id,
        detail_level=detail_level,
        user_prompt=user_prompt,
        rag_enabled=1 if rag_enabled else 0,
        status=TaskStatus.CREATED,
        current_step=None,
        progress=0,
        page_count_estimated=_estimate_page_count(detail_level),
        idempotency_key=idempotency_key,
    )

    db.add(task)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppException(status_code=409, code=1005, message='idempotency conflict') from exc

    add_task_event(db, task_id=task.id, event_type=TaskEventType.STATUS_CHANGED, message='task created')

    task.status = TaskStatus.VALIDATING
    task.current_step = 'validate_input'
    task.progress = 2
    add_task_event(db, task_id=task.id, event_type=TaskEventType.STATUS_CHANGED, message='task validating')

    task.status = TaskStatus.QUEUED
    task.current_step = None
    task.progress = 5
    add_task_event(db, task_id=task.id, event_type=TaskEventType.STATUS_CHANGED, message='task queued')

    db.commit()
    db.refresh(task)

    enqueue_ok = enqueue_task(task.task_no)
    if not enqueue_ok:
        db.add(
            TaskEvent(
                task_id=task.id,
                event_type=TaskEventType.WARNING,
                message='task queued without redis stream, worker may fallback to db scan',
                payload_json={'task_no': task.task_no},
            )
        )
        db.commit()

    cache_task_progress(
        task.task_no,
        {
            'status': task.status,
            'current_step': task.current_step,
            'progress': task.progress,
            'updated_at': task.updated_at,
        },
    )

    push_task_event_cache(task.task_no, 'task queued')
    return task


def get_task_by_no(db: Session, *, user_id: int, task_no: str) -> Task:
    task = db.scalar(select(Task).where(Task.user_id == user_id, Task.task_no == task_no))
    if not task:
        raise AppException(status_code=404, code=1002, message='task not found')
    return task


def list_tasks(db: Session, *, user_id: int, limit: int = 20) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.user_id == user_id)
        .order_by(desc(Task.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def list_task_events(
    db: Session,
    *,
    task_id: int,
    cursor: int | None,
    limit: int,
) -> tuple[list[TaskEvent], int | None]:
    stmt = select(TaskEvent).where(TaskEvent.task_id == task_id)
    if cursor:
        stmt = stmt.where(TaskEvent.id < cursor)
    stmt = stmt.order_by(desc(TaskEvent.id)).limit(limit)

    items = list(db.scalars(stmt).all())
    next_cursor = items[-1].id if items else None
    return items, next_cursor


def list_task_steps(db: Session, *, task_id: int) -> list[TaskStep]:
    stmt = select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.step_order.asc())
    return list(db.scalars(stmt).all())


def list_task_events_ascending(
    db: Session,
    *,
    task_id: int,
    limit: int,
) -> tuple[list[TaskEvent], int | None]:
    stmt = (
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(asc(TaskEvent.event_time), asc(TaskEvent.id))
        .limit(limit)
    )
    items = list(db.scalars(stmt).all())
    next_cursor = items[-1].id if items else None
    return items, next_cursor


def get_task_replay(
    db: Session,
    *,
    user_id: int,
    task_no: str,
    limit: int = 50,
) -> tuple[Task, list[TaskStep], list[TaskEvent], int | None]:
    task = get_task_by_no(db, user_id=user_id, task_no=task_no)
    steps = list_task_steps(db, task_id=task.id)
    events, next_cursor = list_task_events_ascending(db, task_id=task.id, limit=limit)
    return task, steps, events, next_cursor


def _normalize_mapping_attempt_no(db: Session, *, task_id: int, attempt_no: str | int | None) -> int | None:
    if attempt_no is None:
        attempt_no = 'latest'

    if isinstance(attempt_no, str) and attempt_no.strip().lower() == 'latest':
        try:
            value = db.scalar(
                select(func.max(TaskPageMapping.attempt_no)).where(TaskPageMapping.task_id == task_id)
            )
        except Exception:
            return None
        return int(value) if value is not None else None

    try:
        parsed = int(attempt_no)
    except Exception as exc:
        raise AppException(status_code=400, code=1001, message='invalid attempt_no') from exc
    return max(1, parsed)


def _encode_mapping_cursor(row_id: int) -> str:
    payload = json.dumps({'id': int(row_id)}, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def _decode_mapping_cursor(cursor: str | int | None) -> int | None:
    if cursor is None:
        return None

    raw_cursor = str(cursor).strip()
    if not raw_cursor:
        return None
    if raw_cursor.isdigit():
        return max(0, int(raw_cursor))

    try:
        padded = raw_cursor + '=' * (-len(raw_cursor) % 4)
        payload = base64.urlsafe_b64decode(padded.encode('ascii'))
        decoded = json.loads(payload.decode('utf-8'))
        if isinstance(decoded, dict) and decoded.get('id') is not None:
            return max(0, int(decoded['id']))
    except Exception as exc:
        raise AppException(status_code=400, code=1001, message='invalid cursor') from exc

    raise AppException(status_code=400, code=1001, message='invalid cursor')


def get_task_quality_report(db: Session, *, task_id: int) -> TaskQualityReport | None:
    stmt = (
        select(TaskQualityReport)
        .where(TaskQualityReport.task_id == task_id)
        .order_by(TaskQualityReport.created_at.desc(), TaskQualityReport.id.desc())
        .limit(1)
    )
    try:
        return db.scalar(stmt)
    except Exception:
        return None


def list_task_page_mappings(
    db: Session,
    *,
    task_id: int,
    attempt_no: str | int | None,
    cursor: str | int | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int | None, str | None]:
    resolved_attempt_no = _normalize_mapping_attempt_no(db, task_id=task_id, attempt_no=attempt_no)
    if resolved_attempt_no is None:
        return [], None, None

    parsed_cursor = _decode_mapping_cursor(cursor)

    stmt = (
        select(TaskPageMapping)
        .where(
            TaskPageMapping.task_id == task_id,
            TaskPageMapping.attempt_no == resolved_attempt_no,
        )
        .order_by(TaskPageMapping.id.asc())
        .limit(limit)
    )
    if parsed_cursor is not None:
        stmt = stmt.where(TaskPageMapping.id > parsed_cursor)

    try:
        rows = list(db.scalars(stmt).all())
    except Exception:
        return [], resolved_attempt_no, None

    if not rows:
        return [], resolved_attempt_no, None

    slide_nos = sorted({row.slide_no for row in rows})
    slot_fillings_by_slide: dict[int, list[dict[str, str]]] = {}
    if slide_nos:
        slot_stmt = (
            select(TaskSlotFilling)
            .where(
                TaskSlotFilling.task_id == task_id,
                TaskSlotFilling.attempt_no == resolved_attempt_no,
                TaskSlotFilling.slide_no.in_(slide_nos),
            )
            .order_by(TaskSlotFilling.slide_no.asc(), TaskSlotFilling.slot_key.asc())
        )
        try:
            slot_rows = list(db.scalars(slot_stmt).all())
        except Exception:
            slot_rows = []
        for slot_row in slot_rows:
            slot_fillings_by_slide.setdefault(slot_row.slide_no, []).append(
                {
                    'slot_key': slot_row.slot_key,
                    'fill_status': slot_row.fill_status,
                }
            )

    items = [
        {
            'slide_no': row.slide_no,
            'template_page_no': row.template_page_no,
            'fallback_level': row.fallback_level,
            'slot_fillings': slot_fillings_by_slide.get(row.slide_no, []),
        }
        for row in rows
    ]
    next_cursor = _encode_mapping_cursor(rows[-1].id) if len(rows) >= limit else None
    return items, resolved_attempt_no, next_cursor


def retry_task(db: Session, *, task: Task) -> Task:
    if task.status != TaskStatus.FAILED:
        raise AppException(status_code=409, code=1004, message='task status does not allow retry')
    if task.retry_count >= 3:
        raise AppException(status_code=409, code=1004, message='retry limit exceeded')

    task.retry_count += 1
    task.status = TaskStatus.QUEUED
    task.current_step = None
    task.progress = 5
    task.error_code = None
    task.error_message = None
    task.finished_at = None

    add_task_event(
        db,
        task_id=task.id,
        event_type=TaskEventType.STATUS_CHANGED,
        message='task retried and queued',
        payload_json={'retry_count': task.retry_count},
    )
    db.commit()
    db.refresh(task)

    enqueue_task(task.task_no)
    return task


def cancel_task(db: Session, *, task: Task) -> Task:
    if task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        raise AppException(status_code=409, code=1004, message='task status does not allow cancel')

    task.status = TaskStatus.CANCELED
    task.current_step = None
    add_task_event(db, task_id=task.id, event_type=TaskEventType.STATUS_CHANGED, message='task canceled')
    db.commit()
    db.refresh(task)
    return task


def _get_task_delete_event(db: Session, *, task_id: int) -> TaskEvent | None:
    stmt = (
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type == TaskEventType.WARNING,
            TaskEvent.message == 'task deleted',
        )
        .order_by(TaskEvent.id.desc())
        .limit(1)
    )
    try:
        return db.scalar(stmt)
    except Exception:
        return None


def _list_task_preview_files(db: Session, *, task: Task) -> list[File]:
    prefix = f'results/{task.user_id}/{task.task_no}/preview/'
    stmt = (
        select(File)
        .where(
            File.user_id == task.user_id,
            File.file_role == FileRole.ASSET_IMAGE,
            File.storage_path.like(f'{prefix}%'),
        )
        .order_by(File.filename.asc())
    )
    return list(db.scalars(stmt).all())


def delete_task(db: Session, *, user_id: int, task_no: str) -> dict[str, Any]:
    task = get_task_by_no(db, user_id=user_id, task_no=task_no)
    existing_delete_event = _get_task_delete_event(db, task_id=task.id)
    if existing_delete_event:
        payload = existing_delete_event.payload_json or {}
        if task.status != TaskStatus.CANCELED:
            task.status = TaskStatus.CANCELED
            task.current_step = None
            task.result_file_id = None
            db.commit()
        return {
            'task_no': task.task_no,
            'status': task.status,
            'message': 'task already deleted',
            'deleted_at': payload.get('deleted_at'),
            'cleaned_file_count': int(payload.get('cleaned_file_count') or 0),
        }

    cleaned_file_count = 0
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    result_file_deleted = False
    preview_file_deleted_count = 0

    if task.result_file_id:
        result_file = db.get(File, task.result_file_id)
        if result_file and result_file.user_id == user_id:
            delete_summary = delete_file(db, user_id=user_id, file_id=result_file.id)
            result_file_deleted = delete_summary.get('message') == 'file deleted'
            if result_file_deleted:
                cleaned_file_count += 1

    for preview_file in _list_task_preview_files(db, task=task):
        delete_summary = delete_file(db, user_id=user_id, file_id=preview_file.id)
        if delete_summary.get('message') == 'file deleted':
            preview_file_deleted_count += 1
            cleaned_file_count += 1

    task.status = TaskStatus.CANCELED
    task.current_step = None
    task.result_file_id = None
    add_task_event(
        db,
        task_id=task.id,
        event_type=TaskEventType.WARNING,
        message='task deleted',
        payload_json={
            'operator': 'system',
            'user_id': user_id,
            'task_no': task.task_no,
            'deleted_at': deleted_at.isoformat(timespec='seconds'),
            'cleaned_file_count': cleaned_file_count,
            'result_file_deleted': result_file_deleted,
            'preview_file_deleted_count': preview_file_deleted_count,
        },
    )
    db.commit()
    db.refresh(task)
    return {
        'task_no': task.task_no,
        'status': task.status,
        'message': 'task deleted',
        'deleted_at': deleted_at,
        'cleaned_file_count': cleaned_file_count,
    }
