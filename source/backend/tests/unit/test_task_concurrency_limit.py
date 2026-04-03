from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppException
from app.db.base import Base
from app.models import File, Task, User
from app.services.task_service import _check_create_task_concurrency_limit


class TaskConcurrencyLimitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._seq = 0

    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'task_concurrency_limit.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _create_user_and_files(self, db: Session) -> tuple[User, File, File]:
        user = User(username='limit-user', email='limit-user@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()

        source = File(
            user_id=user.id,
            file_role='pdf_source',
            storage_provider='local',
            storage_path='uploads/u/source.pdf',
            filename='source.pdf',
            ext='pdf',
            mime_type='application/pdf',
            file_size=1024,
            status='uploaded',
            scan_status='clean',
        )
        reference = File(
            user_id=user.id,
            file_role='ppt_reference',
            storage_provider='local',
            storage_path='uploads/u/reference.pptx',
            filename='reference.pptx',
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=2048,
            status='uploaded',
            scan_status='clean',
        )
        db.add_all([source, reference])
        db.flush()
        return user, source, reference

    def _create_task(self, db: Session, *, user: User, source: File, reference: File, status: str, updated_at: datetime) -> Task:
        self._seq += 1
        task = Task(
            user_id=user.id,
            task_no=f'T_LIMIT_{status}_{int(updated_at.timestamp())}_{self._seq}',
            source_file_id=source.id,
            reference_file_id=reference.id,
            detail_level='balanced',
            rag_enabled=0,
            status=status,
            progress=10,
            updated_at=updated_at,
        )
        db.add(task)
        db.flush()
        return task

    def test_raises_when_active_tasks_exceed_limit(self) -> None:
        with self._make_session() as db:
            user, source, reference = self._create_user_and_files(db)
            now = datetime.utcnow()
            self._create_task(db, user=user, source=source, reference=reference, status='running', updated_at=now)
            self._create_task(db, user=user, source=source, reference=reference, status='queued', updated_at=now)

            with patch('app.services.task_service.get_settings') as mock_settings:
                mock_settings.return_value.task_concurrency_per_user = 2
                mock_settings.return_value.task_concurrency_active_window_minutes = 120
                with self.assertRaises(AppException) as ctx:
                    _check_create_task_concurrency_limit(db, user.id)

            self.assertEqual(ctx.exception.status_code, 429)
            self.assertEqual(ctx.exception.code, 1004)

    def test_ignores_stale_active_tasks_outside_window(self) -> None:
        with self._make_session() as db:
            user, source, reference = self._create_user_and_files(db)
            stale = datetime.utcnow() - timedelta(hours=5)
            self._create_task(db, user=user, source=source, reference=reference, status='running', updated_at=stale)
            self._create_task(db, user=user, source=source, reference=reference, status='queued', updated_at=stale)

            with patch('app.services.task_service.get_settings') as mock_settings:
                mock_settings.return_value.task_concurrency_per_user = 2
                mock_settings.return_value.task_concurrency_active_window_minutes = 120
                _check_create_task_concurrency_limit(db, user.id)


if __name__ == '__main__':
    unittest.main()
