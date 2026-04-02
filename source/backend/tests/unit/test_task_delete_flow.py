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

from app.services.task_service import delete_task


class TaskDeleteFlowTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'task_delete_flow.sqlite3'
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
            else ('image/svg+xml' if filename.endswith('.svg') else 'application/pdf'),
            file_size=16,
            checksum_sha256='x',
            status=status,
            scan_status='clean',
            scan_report_json={'scan_status': 'clean'},
        )
        db.add(file)
        db.flush()
        return file

    def test_delete_task_cleans_result_and_preview_files_and_is_idempotent(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            owner = self._seed_user(db, 'task-delete-owner')
            source_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='pdf_source',
                storage_path='uploads/1/source.pdf',
                filename='source.pdf',
            )
            reference_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='ppt_reference',
                storage_path='uploads/1/reference.pptx',
                filename='reference.pptx',
            )
            result_storage_path = 'results/1/T_DELETE_TASK_001/result.pptx'
            preview_storage_root = Path(storage_root) / 'results/1/T_DELETE_TASK_001/preview'
            preview_storage_root.mkdir(parents=True, exist_ok=True)
            result_path = Path(storage_root) / result_storage_path
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_bytes(b'result-bytes')
            preview_path_1 = preview_storage_root / 'page_001.svg'
            preview_path_2 = preview_storage_root / 'page_002.svg'
            preview_path_1.write_text('<svg>1</svg>', encoding='utf-8')
            preview_path_2.write_text('<svg>2</svg>', encoding='utf-8')
            result_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='ppt_result',
                storage_path=result_storage_path,
                filename='result.pptx',
            )
            preview_file_1 = self._seed_file(
                db,
                user_id=owner.id,
                file_role='asset_image',
                storage_path='results/1/T_DELETE_TASK_001/preview/page_001.svg',
                filename='page_001.svg',
            )
            preview_file_2 = self._seed_file(
                db,
                user_id=owner.id,
                file_role='asset_image',
                storage_path='results/1/T_DELETE_TASK_001/preview/page_002.svg',
                filename='page_002.svg',
            )
            task = Task(
                user_id=owner.id,
                task_no='T_DELETE_TASK_001',
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
                summary = delete_task(db, user_id=owner.id, task_no=task.task_no)
                db.refresh(task)
                db.refresh(result_file)
                db.refresh(preview_file_1)
                db.refresh(preview_file_2)

                self.assertEqual(summary['task_no'], task.task_no)
                self.assertEqual(summary['status'], 'canceled')
                self.assertEqual(summary['message'], 'task deleted')
                self.assertEqual(summary['cleaned_file_count'], 3)
                self.assertEqual(task.status, 'canceled')
                self.assertIsNone(task.result_file_id)
                self.assertEqual(result_file.status, 'deleted')
                self.assertEqual(preview_file_1.status, 'deleted')
                self.assertEqual(preview_file_2.status, 'deleted')
                self.assertFalse(result_path.exists())
                self.assertFalse(preview_path_1.exists())
                self.assertFalse(preview_path_2.exists())

                task_delete_events = list(
                    db.scalars(
                        select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.message == 'task deleted')
                    ).all()
                )
                self.assertEqual(len(task_delete_events), 1)
                self.assertEqual(task_delete_events[0].payload_json['cleaned_file_count'], 3)
                self.assertEqual(task_delete_events[0].payload_json['task_no'], task.task_no)
                self.assertEqual(task_delete_events[0].payload_json['user_id'], owner.id)

                repeat_summary = delete_task(db, user_id=owner.id, task_no=task.task_no)
                self.assertEqual(repeat_summary['status'], 'canceled')
                self.assertEqual(repeat_summary['message'], 'task already deleted')
                self.assertEqual(repeat_summary['cleaned_file_count'], 3)
                self.assertEqual(
                    len(
                        list(
                            db.scalars(
                                select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.message == 'task deleted')
                            ).all()
                        )
                    ),
                    1,
                )

    def test_delete_task_by_other_user_is_rejected(self) -> None:
        with self._make_session() as db, tempfile.TemporaryDirectory() as storage_root:
            owner = self._seed_user(db, 'task-delete-owner-a')
            intruder = self._seed_user(db, 'task-delete-owner-b')
            source_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='pdf_source',
                storage_path='uploads/1/source.pdf',
                filename='source.pdf',
            )
            reference_file = self._seed_file(
                db,
                user_id=owner.id,
                file_role='ppt_reference',
                storage_path='uploads/1/reference.pptx',
                filename='reference.pptx',
            )
            task = Task(
                user_id=owner.id,
                task_no='T_DELETE_TASK_002',
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                detail_level='balanced',
                rag_enabled=0,
                status='succeeded',
                progress=100,
            )
            db.add(task)
            db.commit()

            settings = self._make_settings(storage_root)
            with patch('app.services.file_service.get_settings', return_value=settings):
                with self.assertRaises(AppException) as ctx:
                    delete_task(db, user_id=intruder.id, task_no=task.task_no)

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.code, 1002)
            self.assertIn('task not found', ctx.exception.message)


if __name__ == '__main__':
    unittest.main()
