from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.constants import TaskStatus, TaskStepCode
from app.db.base import Base
from app.models import File, Task, TaskStep, User
from app.services.metrics_service import get_metrics_overview


class MetricsServiceTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'metrics_service.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _create_user_and_files(self, db: Session) -> tuple[User, File, File]:
        user = User(username='metrics-user', email='metrics@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()

        source_file = File(
            user_id=user.id,
            file_role='pdf_source',
            storage_provider='local',
            storage_path='/tmp/source.pdf',
            filename='source.pdf',
            ext='pdf',
            mime_type='application/pdf',
            file_size=1024,
            checksum_sha256=None,
            status='uploaded',
        )
        reference_file = File(
            user_id=user.id,
            file_role='ppt_reference',
            storage_provider='local',
            storage_path='/tmp/reference.pptx',
            filename='reference.pptx',
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=2048,
            checksum_sha256=None,
            status='uploaded',
        )
        db.add_all([source_file, reference_file])
        db.flush()
        return user, source_file, reference_file

    def _create_task(
        self,
        db: Session,
        *,
        user_id: int,
        source_file_id: int,
        reference_file_id: int,
        task_no: str,
        status: TaskStatus,
        created_at: datetime,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> Task:
        task = Task(
            user_id=user_id,
            task_no=task_no,
            source_file_id=source_file_id,
            reference_file_id=reference_file_id,
            detail_level='balanced',
            user_prompt=None,
            rag_enabled=0,
            status=status,
            current_step=None,
            progress=100 if status == TaskStatus.SUCCEEDED else 0,
            page_count_estimated=16,
            page_count_final=16 if status == TaskStatus.SUCCEEDED else None,
            error_code=None,
            error_message=None,
            retry_count=0,
            idempotency_key=None,
            started_at=started_at,
            finished_at=finished_at,
            created_at=created_at,
        )
        db.add(task)
        db.flush()
        return task

    def test_get_metrics_overview_includes_quality_observability_summary(self) -> None:
        with self._make_session() as db:
            user, source_file, reference_file = self._create_user_and_files(db)
            now = datetime.utcnow()

            task_1 = self._create_task(
                db,
                user_id=user.id,
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                task_no='T001',
                status=TaskStatus.SUCCEEDED,
                created_at=now - timedelta(hours=1),
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=19, seconds=30),
            )
            task_2 = self._create_task(
                db,
                user_id=user.id,
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                task_no='T002',
                status=TaskStatus.SUCCEEDED,
                created_at=now - timedelta(hours=2),
                started_at=now - timedelta(minutes=18),
                finished_at=now - timedelta(minutes=17, seconds=20),
            )
            task_3 = self._create_task(
                db,
                user_id=user.id,
                source_file_id=source_file.id,
                reference_file_id=reference_file.id,
                task_no='T003',
                status=TaskStatus.FAILED,
                created_at=now - timedelta(hours=3),
            )

            db.add_all(
                [
                    TaskStep(
                        task_id=task_1.id,
                        step_code=TaskStepCode.SELF_CORRECT,
                        step_order=7,
                        step_status='succeeded',
                        input_json=None,
                        output_json={
                            'quality_report': {
                                'risk_score': 0.82,
                                'overflow': True,
                                'collision': False,
                                'empty_space': False,
                                'alignment_risk': True,
                                'density_imbalance': False,
                                'title_consistency': False,
                                'signals': {
                                    'flags': {
                                        'overflow': True,
                                        'alignment_risk': True,
                                        'title_consistency': False,
                                    }
                                },
                            }
                        },
                        started_at=now - timedelta(minutes=19),
                        finished_at=now - timedelta(minutes=19, seconds=5),
                        duration_ms=5000,
                        error_code=None,
                        error_message=None,
                    ),
                    TaskStep(
                        task_id=task_2.id,
                        step_code=TaskStepCode.SELF_CORRECT,
                        step_order=7,
                        step_status='succeeded',
                        input_json=None,
                        output_json={
                            'quality_report': {
                                'risk_score': 0.31,
                                'overflow': False,
                                'collision': True,
                                'empty_space': True,
                                'alignment_risk': False,
                                'density_imbalance': False,
                                'title_consistency': True,
                                'signals': {
                                    'flags': {
                                        'collision': True,
                                        'empty_space': True,
                                        'title_consistency': True,
                                    }
                                },
                            }
                        },
                        started_at=now - timedelta(minutes=17),
                        finished_at=now - timedelta(minutes=17, seconds=3),
                        duration_ms=3000,
                        error_code=None,
                        error_message=None,
                    ),
                ]
            )
            db.commit()

            result = get_metrics_overview(db, user_id=user.id, days=7)

            self.assertEqual(result['total_tasks'], 3)
            self.assertEqual(result['success_tasks'], 2)
            self.assertEqual(result['failed_tasks'], 1)
            self.assertAlmostEqual(result['self_correct_coverage'], 2 / 3, places=4)
            self.assertAlmostEqual(result['avg_quality_risk'], (0.82 + 0.31) / 2, places=4)
            self.assertEqual(result['high_risk_tasks'], 1)

            flags = {item['signal']: item['count'] for item in result['quality_flags_top']}
            self.assertEqual(flags['overflow'], 1)
            self.assertEqual(flags['collision'], 1)
            self.assertEqual(flags['empty_space'], 1)
            self.assertEqual(flags['alignment_risk'], 1)
            self.assertEqual(flags['title_consistency'], 1)


if __name__ == '__main__':
    unittest.main()
