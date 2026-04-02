from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import File, Task, TaskPageMapping, TaskSlotFilling, User

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICES_DIR = _REPO_ROOT / 'source' / 'backend' / 'app' / 'services'
if 'app.services' not in sys.modules:
    services_pkg = ModuleType('app.services')
    services_pkg.__path__ = [str(_SERVICES_DIR)]  # type: ignore[attr-defined]
    sys.modules['app.services'] = services_pkg

from app.services.task_service import list_task_page_mappings


class TaskMappingsCursorTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'task_mappings_cursor.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _seed_task_with_mappings(self, db: Session) -> tuple[Task, list[TaskPageMapping]]:
        user = User(username='cursor-user', email='cursor@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()

        source_file = File(
            user_id=user.id,
            file_role='pdf_source',
            storage_provider='local',
            storage_path='uploads/source.pdf',
            filename='source.pdf',
            ext='pdf',
            mime_type='application/pdf',
            file_size=128,
            checksum_sha256='x',
            status='uploaded',
            scan_status='clean',
        )
        reference_file = File(
            user_id=user.id,
            file_role='ppt_reference',
            storage_provider='local',
            storage_path='uploads/reference.pptx',
            filename='reference.pptx',
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=256,
            checksum_sha256='y',
            status='uploaded',
            scan_status='clean',
        )
        db.add(source_file)
        db.add(reference_file)
        db.flush()

        task = Task(
            user_id=user.id,
            task_no='T_CURSOR_001',
            source_file_id=source_file.id,
            reference_file_id=reference_file.id,
            detail_level='balanced',
            rag_enabled=0,
            status='succeeded',
            progress=100,
        )
        db.add(task)
        db.flush()

        rows: list[TaskPageMapping] = []
        for slide_no in (1, 2, 3):
            row = TaskPageMapping(
                task_id=task.id,
                attempt_no=1,
                slide_no=slide_no,
                page_function='content',
                template_page_no=slide_no,
                mapping_score=0.95,
                fallback_level=0,
                mapping_json={'slide_no': slide_no},
            )
            db.add(row)
            db.flush()
            rows.append(row)
            db.add(
                TaskSlotFilling(
                    task_id=task.id,
                    attempt_no=1,
                    slide_no=slide_no,
                    slot_key='title',
                    slot_type='text',
                    content_source='llm_text',
                    fill_status='success',
                    quality_score=0.95,
                    overflow_flag=0,
                    overlap_flag=0,
                    fill_json={'value': f'slide-{slide_no}'},
                )
            )

        db.commit()
        return task, rows

    def test_mappings_returns_opaque_cursor_and_supports_opaque_follow_page(self) -> None:
        with self._make_session() as db:
            task, _ = self._seed_task_with_mappings(db)

            items, resolved_attempt_no, next_cursor = list_task_page_mappings(
                db,
                task_id=task.id,
                attempt_no='latest',
                cursor=None,
                limit=2,
            )

            self.assertEqual(resolved_attempt_no, 1)
            self.assertEqual(len(items), 2)
            self.assertIsInstance(next_cursor, str)
            assert next_cursor is not None
            self.assertFalse(next_cursor.isdigit())

            items2, resolved_attempt_no2, next_cursor2 = list_task_page_mappings(
                db,
                task_id=task.id,
                attempt_no='latest',
                cursor=next_cursor,
                limit=2,
            )

            self.assertEqual(resolved_attempt_no2, 1)
            self.assertEqual(len(items2), 1)
            self.assertEqual(items2[0]['slide_no'], 3)
            self.assertIsNone(next_cursor2)

    def test_mappings_supports_legacy_integer_cursor(self) -> None:
        with self._make_session() as db:
            task, rows = self._seed_task_with_mappings(db)
            legacy_cursor = str(rows[1].id)

            items, resolved_attempt_no, next_cursor = list_task_page_mappings(
                db,
                task_id=task.id,
                attempt_no='latest',
                cursor=legacy_cursor,
                limit=10,
            )

            self.assertEqual(resolved_attempt_no, 1)
            self.assertEqual([item['slide_no'] for item in items], [3])
            self.assertIsNone(next_cursor)


if __name__ == '__main__':
    unittest.main()
