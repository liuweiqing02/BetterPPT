from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppException
from app.db.base import Base
from app.models import File, Task, User
from app.services.file_service import complete_upload, create_upload_slot, save_uploaded_bytes
from app.services.task_service import create_task
from app.workers.runner import _write_preview_files, _write_result_file


def _make_pdf_bytes() -> bytes:
    return (
        b'%PDF-1.4\n'
        b'1 0 obj\n<< /Type /Catalog >>\nendobj\n'
        b'xref\n0 1\n0000000000 65535 f \n'
        b'trailer\n<< /Root 1 0 R >>\nstartxref\n0\n%%EOF\n'
    )


def _make_pptx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        zf.writestr(
            'ppt/presentation.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"></p:presentation>',
        )
    return buffer.getvalue()


class FileScanGateTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'file_scan_gate.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _make_settings(self, root_dir: str):
        from app.core.config import get_settings

        return get_settings().model_copy(update={'local_storage_root': root_dir})

    def _create_user(self, db: Session) -> User:
        user = User(username='scan-gate-user', email='scan-gate@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()
        return user

    def _create_uploaded_file(
        self,
        db: Session,
        *,
        user_id: int,
        filename: str,
        file_role: str,
        content_type: str,
        content: bytes,
        root_dir: str,
    ) -> File:
        settings = self._make_settings(root_dir)
        with patch('app.services.file_service.get_settings', return_value=settings):
            file, _ = create_upload_slot(
                db,
                user_id=user_id,
                filename=filename,
                file_role=file_role,
                content_type=content_type,
                file_size=len(content),
                base_url='http://testserver',
            )
            save_uploaded_bytes(file, content)
            checksum = hashlib.sha256(content).hexdigest()
            completed = complete_upload(db, user_id=user_id, file_id=file.id, checksum_sha256=checksum)
        return completed

    def test_complete_upload_marks_clean_files_and_allows_task_creation(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            user = self._create_user(db)
            before = datetime.now(timezone.utc)

            source_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='source.pdf',
                file_role='pdf_source',
                content_type='application/pdf',
                content=_make_pdf_bytes(),
                root_dir=storage_root,
            )
            reference_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='reference.pptx',
                file_role='ppt_reference',
                content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                content=_make_pptx_bytes(),
                root_dir=storage_root,
            )

            self.assertEqual(source_file.scan_status, 'clean')
            self.assertEqual(reference_file.scan_status, 'clean')
            self.assertEqual(source_file.scan_report_json['scan_status'], 'clean')
            self.assertEqual(reference_file.scan_report_json['scan_status'], 'clean')
            self.assertIsNotNone(source_file.retention_expire_at)
            self.assertIsNotNone(reference_file.retention_expire_at)
            self.assertGreaterEqual(source_file.retention_expire_at, (before + timedelta(days=6, hours=23)).replace(tzinfo=None))
            self.assertLessEqual(source_file.retention_expire_at, (before + timedelta(days=7, hours=1)).replace(tzinfo=None))
            self.assertGreaterEqual(reference_file.retention_expire_at, (before + timedelta(days=6, hours=23)).replace(tzinfo=None))
            self.assertLessEqual(reference_file.retention_expire_at, (before + timedelta(days=7, hours=1)).replace(tzinfo=None))

            task = create_task(
                db,
                user_id=user.id,
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                detail_level='balanced',
                user_prompt='test prompt',
                rag_enabled=False,
                idempotency_key=None,
            )

            self.assertEqual(task.status, 'queued')
            self.assertEqual(task.source_file_id, source_file.id)
            self.assertEqual(task.reference_file_id, reference_file.id)

    def test_result_and_preview_files_get_thirty_day_retention(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            user = self._create_user(db)
            source_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='source.pdf',
                file_role='pdf_source',
                content_type='application/pdf',
                content=_make_pdf_bytes(),
                root_dir=storage_root,
            )
            reference_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='reference.pptx',
                file_role='ppt_reference',
                content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                content=_make_pptx_bytes(),
                root_dir=storage_root,
            )
            task = Task(
                user_id=user.id,
                task_no='T_RETENTION_001',
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                detail_level='balanced',
                rag_enabled=0,
                status='succeeded',
                progress=100,
            )
            db.add(task)
            db.commit()
            db.refresh(task)

            settings = self._make_settings(storage_root)
            before = datetime.now(timezone.utc)
            with patch('app.workers.runner.get_settings', return_value=settings):
                result_file, _ = _write_result_file(
                    db,
                    task,
                    filename='result.pptx',
                    slide_plan=[{'page_no': 1, 'title': 'Slide 1', 'bullets': ['A']}],
                )
                preview_files = _write_preview_files(db, task, total_pages=1, profile_id=None)
                db.commit()
                db.refresh(result_file)
                preview_file = db.get(File, preview_files[0]['file_id'])

            self.assertIsNotNone(result_file.retention_expire_at)
            assert preview_file is not None
            self.assertIsNotNone(preview_file.retention_expire_at)
            lower = (before + timedelta(days=29, hours=23)).replace(tzinfo=None)
            upper = (before + timedelta(days=30, hours=1)).replace(tzinfo=None)
            self.assertGreaterEqual(result_file.retention_expire_at, lower)
            self.assertLessEqual(result_file.retention_expire_at, upper)
            self.assertGreaterEqual(preview_file.retention_expire_at, lower)
            self.assertLessEqual(preview_file.retention_expire_at, upper)

    def test_blocked_scan_prevents_task_creation(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            user = self._create_user(db)

            source_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='source.pdf',
                file_role='pdf_source',
                content_type='application/pdf',
                content=_make_pdf_bytes(),
                root_dir=storage_root,
            )
            reference_file = self._create_uploaded_file(
                db,
                user_id=user.id,
                filename='reference.pptx',
                file_role='ppt_reference',
                content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                content=b'not-a-valid-pptx',
                root_dir=storage_root,
            )

            self.assertEqual(source_file.scan_status, 'clean')
            self.assertEqual(reference_file.scan_status, 'blocked')
            self.assertEqual(reference_file.scan_report_json['scan_status'], 'blocked')
            self.assertIn('reason', reference_file.scan_report_json)

            with self.assertRaises(AppException) as ctx:
                create_task(
                    db,
                    user_id=user.id,
                    source_file_id=source_file.id,
                    reference_file_id=reference_file.id,
                    detail_level='balanced',
                    user_prompt=None,
                    rag_enabled=False,
                    idempotency_key=None,
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.code, 1001)
            self.assertIn('reference file must be clean', ctx.exception.message)


if __name__ == '__main__':
    unittest.main()
