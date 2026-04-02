from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import File, Task, TaskEvent, User

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICES_DIR = _REPO_ROOT / 'source' / 'backend' / 'app' / 'services'
if 'app.services' not in sys.modules:
    services_pkg = ModuleType('app.services')
    services_pkg.__path__ = [str(_SERVICES_DIR)]  # type: ignore[attr-defined]
    sys.modules['app.services'] = services_pkg

from app.services.retention_service import cleanup_expired_files, cleanup_expired_task_events


class RetentionServiceTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'retention_service.sqlite3'
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

    def _seed_user(self, db: Session) -> User:
        user = User(username='retention-user', email='retention@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()
        return user

    def _seed_expired_file_and_task(self, db: Session, *, root_dir: str) -> tuple[File, Task, Path]:
        user = self._seed_user(db)
        storage_rel_path = 'results/1/T000000000001/expired-result.pptx'
        file_path = Path(root_dir) / storage_rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b'legacy-result')

        expired_at = datetime.utcnow() - timedelta(days=1)
        file = File(
            user_id=user.id,
            file_role='ppt_result',
            storage_provider='local',
            storage_path=storage_rel_path,
            filename='expired-result.pptx',
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=file_path.stat().st_size,
            checksum_sha256='abc',
            status='uploaded',
            scan_status='clean',
            scan_report_json={'scan_status': 'clean'},
            retention_expire_at=expired_at,
        )
        db.add(file)
        db.flush()

        source_file = File(
            user_id=user.id,
            file_role='pdf_source',
            storage_provider='local',
            storage_path='uploads/1/source.pdf',
            filename='source.pdf',
            ext='pdf',
            mime_type='application/pdf',
            file_size=128,
            checksum_sha256='source',
            status='uploaded',
            scan_status='clean',
        )
        reference_file = File(
            user_id=user.id,
            file_role='ppt_reference',
            storage_provider='local',
            storage_path='uploads/1/reference.pptx',
            filename='reference.pptx',
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=256,
            checksum_sha256='reference',
            status='uploaded',
            scan_status='clean',
        )
        db.add(source_file)
        db.add(reference_file)
        db.flush()

        source_file.storage_path = 'uploads/1/source.pdf'
        reference_file.storage_path = 'uploads/1/reference.pptx'
        db.flush()

        task = Task(
            user_id=user.id,
            task_no='T_RETENTION_001',
            source_file_id=source_file.id,
            reference_file_id=reference_file.id,
            result_file_id=file.id,
            detail_level='balanced',
            rag_enabled=0,
            status='succeeded',
            progress=100,
        )
        db.add(task)
        db.commit()
        db.refresh(file)
        db.refresh(task)
        return file, task, file_path

    def test_cleanup_expired_files_marks_expired_deletes_local_file_and_writes_audit_event(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            file, task, file_path = self._seed_expired_file_and_task(db, root_dir=storage_root)
            settings = self._make_settings(storage_root)

            from unittest.mock import patch

            with patch('app.services.retention_service.get_settings', return_value=settings):
                summary = cleanup_expired_files(db, now=datetime.utcnow(), limit=500, dry_run=False)

            refreshed_file = db.get(File, file.id)
            self.assertIsNotNone(refreshed_file)
            assert refreshed_file is not None
            self.assertEqual(refreshed_file.status, 'expired')
            self.assertFalse(file_path.exists())
            self.assertIn('retention', refreshed_file.scan_report_json or {})
            self.assertEqual(summary['expired_files_count'], 1)
            self.assertEqual(summary['files_deleted_count'], 1)
            self.assertEqual(summary['files_marked_expired_count'], 1)
            self.assertEqual(summary['audit_events_written_count'], 1)

            events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)).all())
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, 'warning')
            self.assertIn('retention cleanup expired file', events[0].message or '')
            self.assertEqual(events[0].payload_json['file_id'], file.id)

    def test_cleanup_expired_task_events_removes_older_than_180_days(self) -> None:
        with self._make_session() as db:
            user = self._seed_user(db)
            source_file = File(
                user_id=user.id,
                file_role='pdf_source',
                storage_provider='local',
                storage_path='uploads/1/source-events.pdf',
                filename='source-events.pdf',
                ext='pdf',
                mime_type='application/pdf',
                file_size=128,
                checksum_sha256='source',
                status='uploaded',
                scan_status='clean',
            )
            reference_file = File(
                user_id=user.id,
                file_role='ppt_reference',
                storage_provider='local',
                storage_path='uploads/1/reference-events.pptx',
                filename='reference-events.pptx',
                ext='pptx',
                mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                file_size=256,
                checksum_sha256='reference',
                status='uploaded',
                scan_status='clean',
            )
            db.add(source_file)
            db.add(reference_file)
            db.flush()
            task = Task(
                user_id=user.id,
                task_no='T_RETENTION_EVENTS',
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                detail_level='balanced',
                rag_enabled=0,
                status='succeeded',
                progress=100,
            )
            db.add(task)
            db.flush()

            old_event = TaskEvent(
                task_id=task.id,
                event_type='warning',
                event_time=datetime.utcnow() - timedelta(days=181),
                message='old event',
                payload_json={'kind': 'old'},
            )
            recent_event = TaskEvent(
                task_id=task.id,
                event_type='warning',
                event_time=datetime.utcnow() - timedelta(days=1),
                message='recent event',
                payload_json={'kind': 'recent'},
            )
            db.add(old_event)
            db.add(recent_event)
            db.commit()

            summary = cleanup_expired_task_events(db, now=datetime.utcnow(), days=180, dry_run=False)

            remaining_events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)).all())
            self.assertEqual(summary['deleted_task_events_count'], 1)
            self.assertEqual(len(remaining_events), 1)
            self.assertEqual(remaining_events[0].message, 'recent event')


if __name__ == '__main__':
    unittest.main()
