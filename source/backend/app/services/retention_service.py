from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import TaskEventType
from app.models import File, Task, TaskEvent
from app.services.event_service import add_task_event


_FILE_RETENTION_EVENT_TYPE = TaskEventType.WARNING
_FILE_RETENTION_MESSAGE = 'retention cleanup expired file'


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return now


def _safe_scan_report(scan_report_json: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(scan_report_json, dict):
        return dict(scan_report_json)
    return {}


def _delete_local_file(storage_path: str | None) -> bool:
    if not storage_path:
        return False

    settings = get_settings()
    path = settings.storage_root_path / storage_path
    if not path.exists():
        return False

    path.unlink()
    return True


def _related_tasks(db: Session, file_id: int) -> list[Task]:
    stmt = select(Task).where(
        or_(
            Task.source_file_id == file_id,
            Task.reference_file_id == file_id,
            Task.result_file_id == file_id,
        )
    )
    return list(db.scalars(stmt).all())


def cleanup_expired_files(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    current_time = _coerce_now(now)
    stmt = (
        select(File)
        .where(
            File.retention_expire_at.is_not(None),
            File.retention_expire_at <= current_time,
            File.status != 'expired',
        )
        .order_by(File.retention_expire_at.asc(), File.id.asc())
        .limit(limit)
    )
    expired_files = list(db.scalars(stmt).all())

    files_deleted = 0
    files_marked_expired = 0
    audit_events_written = 0
    affected_task_ids: set[int] = set()

    for file in expired_files:
        related_tasks = _related_tasks(db, file.id)
        affected_task_ids.update(task.id for task in related_tasks)

        if not dry_run:
            if _delete_local_file(file.storage_path):
                files_deleted += 1

            file.status = 'expired'
            report = _safe_scan_report(file.scan_report_json)
            report['retention'] = {
                'cleaned_at': current_time.isoformat(timespec='seconds'),
                'retention_expire_at': file.retention_expire_at.isoformat(timespec='seconds') if file.retention_expire_at else None,
                'cleanup_action': 'expired',
                'storage_path': file.storage_path,
            }
            file.scan_report_json = report
            files_marked_expired += 1

            for task in related_tasks:
                add_task_event(
                    db,
                    task_id=task.id,
                    event_type=_FILE_RETENTION_EVENT_TYPE,
                    message=_FILE_RETENTION_MESSAGE,
                    payload_json={
                        'file_id': file.id,
                        'file_role': file.file_role,
                        'task_no': task.task_no,
                        'storage_path': file.storage_path,
                        'retention_expire_at': file.retention_expire_at.isoformat(timespec='seconds')
                        if file.retention_expire_at
                        else None,
                        'cleaned_at': current_time.isoformat(timespec='seconds'),
                    },
                )
                audit_events_written += 1

    if not dry_run:
        db.commit()

    return {
        'dry_run': dry_run,
        'checked_files_count': len(expired_files),
        'expired_files_count': len(expired_files),
        'files_deleted_count': files_deleted,
        'files_marked_expired_count': files_marked_expired,
        'audit_events_written_count': audit_events_written,
        'affected_task_count': len(affected_task_ids),
        'timestamp': current_time.isoformat(timespec='seconds'),
    }


def cleanup_expired_task_events(
    db: Session,
    *,
    now: datetime | None = None,
    days: int = 180,
    dry_run: bool = False,
) -> dict[str, Any]:
    current_time = _coerce_now(now)
    cutoff = current_time - timedelta(days=days)

    count_stmt = select(TaskEvent).where(TaskEvent.event_time < cutoff)
    stale_events = list(db.scalars(count_stmt).all())
    stale_count = len(stale_events)

    if not dry_run and stale_count:
        db.execute(delete(TaskEvent).where(TaskEvent.event_time < cutoff))
        db.commit()

    return {
        'dry_run': dry_run,
        'days': days,
        'cutoff': cutoff.isoformat(timespec='seconds'),
        'deleted_task_events_count': stale_count,
        'timestamp': current_time.isoformat(timespec='seconds'),
    }
