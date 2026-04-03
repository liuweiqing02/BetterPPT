from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.tasks import _resolve_fallback_state
from app.core.constants import TaskEventType, TaskStatus
from app.db.base import Base
from app.models import File, Task, TaskEvent, User


class TaskFallbackStateTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'task_fallback_state.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _create_user_and_files(self, db: Session) -> tuple[User, File, File]:
        user = User(username='fallback-user', email='fallback@example.com', password_hash='x', status=1)
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

    def _create_task(self, db: Session, *, status: str, fallback_used: int = 0) -> Task:
        user, source, reference = self._create_user_and_files(db)
        task = Task(
            user_id=user.id,
            task_no='T_FALLBACK_001',
            source_file_id=source.id,
            reference_file_id=reference.id,
            detail_level='balanced',
            rag_enabled=0,
            status=status,
            progress=50,
            fallback_used=fallback_used,
        )
        db.add(task)
        db.flush()
        return task

    def test_resolve_fallback_state_none_without_events(self) -> None:
        with self._make_session() as db:
            task = self._create_task(db, status=TaskStatus.RUNNING, fallback_used=0)
            state, attempt_no = _resolve_fallback_state(db, task)
            self.assertEqual(state, 'none')
            self.assertIsNone(attempt_no)

    def test_resolve_fallback_state_from_latest_fallback_event(self) -> None:
        with self._make_session() as db:
            task = self._create_task(db, status=TaskStatus.RUNNING, fallback_used=1)
            db.add(
                TaskEvent(
                    task_id=task.id,
                    event_type=TaskEventType.FALLBACK_STARTED.value,
                    message='fallback retry started',
                    payload_json={'attempt_no': 2},
                )
            )
            db.flush()

            state, attempt_no = _resolve_fallback_state(db, task)
            self.assertEqual(state, 'running')
            self.assertEqual(attempt_no, 2)

    def test_resolve_fallback_state_legacy_mode_from_task_status(self) -> None:
        with self._make_session() as db:
            task = self._create_task(db, status=TaskStatus.FAILED, fallback_used=1)
            state, attempt_no = _resolve_fallback_state(db, task)
            self.assertEqual(state, 'failed')
            self.assertIsNone(attempt_no)


if __name__ == '__main__':
    unittest.main()
