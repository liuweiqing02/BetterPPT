from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppException
from app.db.base import Base
from app.models import File, Task, TaskEvent, User

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICES_DIR = _REPO_ROOT / 'source' / 'backend' / 'app' / 'services'
if 'app.services' not in sys.modules:
    services_pkg = ModuleType('app.services')
    services_pkg.__path__ = [str(_SERVICES_DIR)]  # type: ignore[attr-defined]
    sys.modules['app.services'] = services_pkg

from app.services.file_service import delete_file


class FileDeleteFlowTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'file_delete_flow.sqlite3'
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

    def _seed_user(self, db: Session, username: str) -> User:
        user = User(username=username, email=f'{username}@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()
        return user

    def _seed_file(
        self,
        db: Session,
        *,
        user_id: int,
        file_role: str,
        storage_path: str,
        filename: str,
        status: str = 'uploaded',
    ) -> File:
        file = File(
            user_id=user_id,
            file_role=file_role,
            storage_provider='local',
            storage_path=storage_path,
            filename=filename,
            ext=filename.rsplit('.', 1)[-1],
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation'
            if filename.endswith('.pptx')
            else 'application/pdf',
            file_size=16,
            checksum_sha256='x',
            status=status,
            scan_status='clean',
            scan_report_json={'scan_status': 'clean'},
        )
        db.add(file)
        db.flush()
        return file

    def test_delete_result_file_soft_deletes_unlinks_task_and_is_idempotent(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            owner = self._seed_user(db, 'delete-owner')
            task_file_user = owner
            source_file = self._seed_file(
                db,
                user_id=task_file_user.id,
                file_role='pdf_source',
                storage_path='uploads/1/source.pdf',
                filename='source.pdf',
            )
            reference_file = self._seed_file(
                db,
                user_id=task_file_user.id,
                file_role='ppt_reference',
                storage_path='uploads/1/reference.pptx',
                filename='reference.pptx',
            )
            result_storage_path = 'results/1/T_DELETE_001/result.pptx'
            result_path = Path(storage_root) / result_storage_path
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_bytes(b'result-file-bytes')
            result_file = self._seed_file(
                db,
                user_id=task_file_user.id,
                file_role='ppt_result',
                storage_path=result_storage_path,
                filename='result.pptx',
                status='uploaded',
            )
            task = Task(
                user_id=task_file_user.id,
                task_no='T_DELETE_001',
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                result_file_id=result_file.id,
                detail_level='balanced',
                rag_enabled=0,
                status='succeeded',
                progress=100,
            )
            db.add(task)
            db.commit()

            settings = self._make_settings(storage_root)
            with patch('app.services.file_service.get_settings', return_value=settings):
                summary = delete_file(db, user_id=owner.id, file_id=result_file.id)
                db.refresh(result_file)
                db.refresh(task)
                self.assertEqual(result_file.status, 'deleted')
                self.assertFalse(result_path.exists())
                self.assertIsNone(task.result_file_id)
                self.assertEqual(summary['status'], 'deleted')
                self.assertEqual(summary['result_file_unlinked_count'], 1)
                self.assertGreaterEqual(summary['related_task_count'], 1)

                events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)).all())
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].event_type, 'warning')
                self.assertEqual(events[0].payload_json['operator'], 'system')
                self.assertEqual(events[0].payload_json['file_id'], result_file.id)
                self.assertEqual(events[0].payload_json['user_id'], owner.id)

                repeat_summary = delete_file(db, user_id=owner.id, file_id=result_file.id)
                self.assertEqual(repeat_summary['status'], 'deleted')
                self.assertEqual(repeat_summary['message'], 'file already deleted')
                self.assertEqual(len(list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)).all())), 1)

    def test_delete_file_by_other_user_is_rejected(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            owner = self._seed_user(db, 'delete-owner-a')
            intruder = self._seed_user(db, 'delete-owner-b')
            file_path = Path(storage_root) / 'uploads/1/source.pdf'
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b'source-file-bytes')
            target_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='pdf_source',
                storage_path='uploads/1/source.pdf',
                filename='source.pdf',
            )
            db.commit()

            settings = self._make_settings(storage_root)
            with patch('app.services.file_service.get_settings', return_value=settings):
                with self.assertRaises(AppException) as ctx:
                    delete_file(db, user_id=intruder.id, file_id=target_file.id)

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.code, 1002)
            self.assertIn('file not found', ctx.exception.message)
            self.assertTrue(file_path.exists())
            self.assertEqual(db.scalar(select(File).where(File.id == target_file.id)).status, 'uploaded')


if __name__ == '__main__':
    unittest.main()
