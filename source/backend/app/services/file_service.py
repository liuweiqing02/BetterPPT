from __future__ import annotations

import hashlib
import hmac
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import FileRole, TaskEventType
from app.core.errors import AppException
from app.models.file import File
from app.models.task import Task
from app.services.event_service import add_task_event

_ALLOWED_MIME = {
    FileRole.PDF_SOURCE: {'application/pdf'},
    FileRole.PPT_REFERENCE: {
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    },
}

_ALLOWED_EXT = {
    FileRole.PDF_SOURCE: {'.pdf'},
    FileRole.PPT_REFERENCE: {'.ppt', '.pptx'},
}

_FALLBACK_MIME = {'', 'application/octet-stream'}
_PDF_MAGIC = b'%PDF-'
_PPT_MAGIC = bytes.fromhex('D0CF11E0A1B11AE1')
_PPTX_REQUIRED_FILES = {'[Content_Types].xml', 'ppt/presentation.xml'}


def _default_retention_expire_at(days: int) -> datetime:
    expire_at = datetime.now(timezone.utc) + timedelta(days=days)
    return expire_at.replace(tzinfo=None)


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _download_signature(file_id: int, user_id: int, expires_at: int) -> str:
    settings = get_settings()
    payload = f'{int(file_id)}:{int(user_id)}:{int(expires_at)}'.encode('utf-8')
    secret = (settings.signed_url_secret or 'dev_signed_url_secret').encode('utf-8')
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def build_signed_download_url(
    *,
    base_url: str,
    file_id: int,
    user_id: int,
    expires_in: int | None = None,
) -> str:
    settings = get_settings()
    ttl = int(expires_in or settings.signed_url_ttl_seconds or 3600)
    ttl = max(30, min(ttl, 24 * 3600))
    expires_at = int(datetime.now(timezone.utc).timestamp()) + ttl
    sig = _download_signature(file_id=file_id, user_id=user_id, expires_at=expires_at)
    query = urlencode({'uid': int(user_id), 'exp': expires_at, 'sig': sig})
    return f"{base_url}/api/v1/files/download/{int(file_id)}?{query}"


def verify_download_signature(*, file_id: int, user_id: int, exp: int | None, sig: str | None) -> bool:
    if exp is None or not sig:
        return False
    try:
        expires_at = int(exp)
    except Exception:
        return False
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if expires_at < now_ts:
        return False
    expected = _download_signature(file_id=file_id, user_id=user_id, expires_at=expires_at)
    return hmac.compare_digest(expected, str(sig))


def _delete_local_file(storage_path: str | None) -> bool:
    if not storage_path:
        return False

    settings = get_settings()
    path = settings.storage_root_path / storage_path
    if not path.exists():
        return False

    path.unlink()
    return True


def _related_tasks(db: Session, *, file_id: int) -> list[Task]:
    stmt = select(Task).where(
        or_(
            Task.source_file_id == file_id,
            Task.reference_file_id == file_id,
            Task.result_file_id == file_id,
        )
    )
    return list(db.scalars(stmt).all())


def _normalize_role(file_role: str) -> str:
    try:
        return FileRole(file_role).value
    except ValueError as exc:
        raise AppException(status_code=400, code=1003, message='unsupported file role') from exc


def _validate_type(filename: str, file_role: str, mime_type: str) -> None:
    role = FileRole(file_role)
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXT[role]:
        raise AppException(status_code=400, code=1003, message='unsupported file extension')

    if mime_type not in _FALLBACK_MIME and mime_type not in _ALLOWED_MIME[role]:
        raise AppException(status_code=400, code=1003, message='unsupported mime type')


def _build_scan_report(
    file: File,
    *,
    scan_status: str,
    reason: str | None,
    checks: dict[str, Any],
    content_sha256: str | None,
) -> dict[str, Any]:
    report = {
        'scan_version': 'basic_v1',
        'scan_status': scan_status,
        'file_id': file.id,
        'file_role': file.file_role,
        'filename': file.filename,
        'ext': file.ext,
        'mime_type': file.mime_type,
        'file_size': file.file_size,
        'content_sha256': content_sha256,
        'scanned_at': datetime.now(timezone.utc).isoformat(),
        'checks': checks,
    }
    if reason:
        report['reason'] = reason
    return report


def _scan_pdf(content: bytes) -> tuple[bool, str | None, dict[str, Any]]:
    checks: dict[str, Any] = {
        'header_ok': content.startswith(_PDF_MAGIC),
        'eof_ok': b'%%EOF' in content[-2048:] if content else False,
    }
    if not checks['header_ok']:
        return False, 'missing_pdf_header', checks
    if not checks['eof_ok']:
        return False, 'missing_pdf_eof_marker', checks
    return True, None, checks


def _scan_ppt(content: bytes) -> tuple[bool, str | None, dict[str, Any]]:
    checks: dict[str, Any] = {
        'ole_header_ok': content.startswith(_PPT_MAGIC),
    }
    if not checks['ole_header_ok']:
        return False, 'missing_ppt_ole_header', checks
    return True, None, checks


def _scan_pptx(path: Path) -> tuple[bool, str | None, dict[str, Any]]:
    checks: dict[str, Any] = {
        'zip_ok': zipfile.is_zipfile(path),
        'required_entries': [],
    }
    if not checks['zip_ok']:
        return False, 'invalid_pptx_zip_container', checks

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except Exception:
        return False, 'invalid_pptx_zip_container', checks

    checks['required_entries'] = sorted(_PPTX_REQUIRED_FILES & names)
    missing = sorted(_PPTX_REQUIRED_FILES - names)
    if missing:
        checks['missing_entries'] = missing
        return False, 'missing_pptx_required_entries', checks
    return True, None, checks


def scan_uploaded_file(db: Session, *, file: File) -> File:
    settings = get_settings()
    full_path = settings.storage_root_path / file.storage_path

    file.scan_status = 'scanning'
    db.flush()

    content: bytes | None = None
    sha256: str | None = None
    if full_path.exists():
        content = full_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()

    if content is None:
        file.scan_status = 'blocked'
        file.scan_report_json = _build_scan_report(
            file,
            scan_status='blocked',
            reason='uploaded_content_missing',
            checks={'file_exists': False},
            content_sha256=None,
        )
        return file

    ext = Path(file.filename).suffix.lower()
    if ext == '.pdf':
        passed, reason, checks = _scan_pdf(content)
    elif ext == '.pptx':
        passed, reason, checks = _scan_pptx(full_path)
    elif ext == '.ppt':
        passed, reason, checks = _scan_ppt(content)
    else:
        passed = False
        reason = 'unsupported_file_extension'
        checks = {'supported': False}

    file.scan_status = 'clean' if passed else 'blocked'
    file.scan_report_json = _build_scan_report(
        file,
        scan_status=file.scan_status,
        reason=reason,
        checks=checks,
        content_sha256=sha256,
    )
    return file


def create_upload_slot(
    db: Session,
    *,
    user_id: int,
    filename: str,
    file_role: str,
    content_type: str,
    file_size: int,
    base_url: str,
) -> tuple[File, dict[str, str]]:
    role = _normalize_role(file_role)
    _validate_type(filename, role, content_type)

    settings = get_settings()
    ext = Path(filename).suffix.lower()
    file = File(
        user_id=user_id,
        file_role=role,
        storage_provider=settings.storage_provider,
        storage_path='pending',
        filename=filename,
        ext=ext.lstrip('.'),
        mime_type=content_type,
        file_size=file_size,
        status='uploading',
    )
    if file.retention_expire_at is None:
        file.retention_expire_at = _default_retention_expire_at(7)
    db.add(file)
    db.flush()

    safe_filename = f'file_{file.id}{ext}'
    local_rel_path = f'{settings.upload_subdir}/{user_id}/{file.id}/{safe_filename}'
    file.storage_path = local_rel_path
    db.commit()
    db.refresh(file)

    upload_url = f'{base_url}/api/v1/files/upload/{file.id}'
    return file, {'Content-Type': content_type, 'Upload-Path': local_rel_path, 'Upload-Url': upload_url}


def get_file_by_id(db: Session, file_id: int) -> File | None:
    return db.get(File, file_id)


def complete_upload(db: Session, *, user_id: int, file_id: int, checksum_sha256: str | None) -> File:
    file = db.get(File, file_id)
    if not file or file.user_id != user_id:
        raise AppException(status_code=404, code=1002, message='file not found')

    if file.status not in {'uploading', 'uploaded'}:
        raise AppException(status_code=409, code=1004, message='file status does not allow complete')

    settings = get_settings()
    full_path = settings.storage_root_path / file.storage_path
    if not full_path.exists():
        file.status = 'uploaded'
        file.scan_status = 'blocked'
        file.scan_report_json = _build_scan_report(
            file,
            scan_status='blocked',
            reason='uploaded_content_missing',
            checks={'file_exists': False},
            content_sha256=None,
        )
        db.commit()
        db.refresh(file)
        return file

    file.checksum_sha256 = checksum_sha256
    file.status = 'uploaded'
    scan_uploaded_file(db, file=file)
    db.commit()
    db.refresh(file)
    return file


def save_uploaded_bytes(file: File, content: bytes) -> None:
    settings = get_settings()
    path = settings.storage_root_path / file.storage_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def get_file_by_user_and_role(db: Session, *, user_id: int, file_id: int, file_role: str) -> File | None:
    stmt = select(File).where(
        File.id == file_id,
        File.user_id == user_id,
        File.file_role == file_role,
    )
    return db.scalar(stmt)


def open_local_file(file: File) -> Path:
    settings = get_settings()
    path = settings.storage_root_path / file.storage_path
    if not path.exists():
        raise AppException(status_code=404, code=1002, message='file content missing')
    return path


def delete_file(
    db: Session,
    *,
    user_id: int,
    file_id: int,
) -> dict[str, Any]:
    file = db.get(File, file_id)
    if not file or file.user_id != user_id:
        raise AppException(status_code=404, code=1002, message='file not found')

    if file.status == 'deleted':
        return {
            'file_id': file.id,
            'status': file.status,
            'message': 'file already deleted',
            'deleted_at': None,
            'related_task_count': 0,
            'result_file_unlinked_count': 0,
        }

    if file.status != 'deleting':
        file.status = 'deleting'
        db.flush()

    deleted_at = _now_naive_utc()
    local_deleted = _delete_local_file(file.storage_path)
    related_tasks = _related_tasks(db, file_id=file.id)
    result_file_unlinked_count = 0

    for task in related_tasks:
        result_file_unlinked = task.result_file_id == file.id
        if task.result_file_id == file.id:
            task.result_file_id = None
            result_file_unlinked_count += 1
        add_task_event(
            db,
            task_id=task.id,
            event_type=TaskEventType.WARNING,
            message='file deleted',
            payload_json={
                'operator': 'system',
                'user_id': user_id,
                'file_id': file.id,
                'file_role': file.file_role,
                'task_no': task.task_no,
                'deleted_at': deleted_at.isoformat(timespec='seconds'),
                'storage_path': file.storage_path,
                'result_file_unlinked': result_file_unlinked,
            },
        )

    file.status = 'deleted'
    report = file.scan_report_json if isinstance(file.scan_report_json, dict) else {}
    report = dict(report)
    report['deletion'] = {
        'deleted_at': deleted_at.isoformat(timespec='seconds'),
        'deleted_local_file': local_deleted,
        'result_file_unlinked_count': result_file_unlinked_count,
        'related_task_count': len(related_tasks),
    }
    file.scan_report_json = report
    db.commit()
    db.refresh(file)

    return {
        'file_id': file.id,
        'status': file.status,
        'message': 'file deleted',
        'deleted_at': deleted_at,
        'related_task_count': len(related_tasks),
        'result_file_unlinked_count': result_file_unlinked_count,
    }
