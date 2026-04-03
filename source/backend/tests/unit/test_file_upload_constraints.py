from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppException
from app.db.base import Base
from app.models import User
from app.services.file_service import create_upload_slot, get_upload_constraints


class FileUploadConstraintsTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / 'file_upload_constraints.sqlite3'
        engine = create_engine(f'sqlite+pysqlite:///{db_path.as_posix()}', connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _create_user(self, db: Session) -> User:
        user = User(username='upload-constraints-user', email='upload-constraints@example.com', password_hash='x', status=1)
        db.add(user)
        db.flush()
        return user

    def test_get_upload_constraints_shape(self) -> None:
        with patch('app.services.file_service.get_settings') as mock_settings:
            mock_settings.return_value.upload_pdf_max_file_size_mb = 88
            mock_settings.return_value.upload_pdf_max_pages = 301
            mock_settings.return_value.upload_reference_ppt_max_file_size_mb = 99
            mock_settings.return_value.upload_reference_ppt_max_pages = 202

            constraints = get_upload_constraints()

        self.assertIn('pdf', constraints)
        self.assertIn('reference_ppt', constraints)
        self.assertEqual(constraints['pdf']['max_file_size_mb'], 88)
        self.assertEqual(constraints['pdf']['max_pages'], 301)
        self.assertEqual(constraints['reference_ppt']['max_file_size_mb'], 99)
        self.assertEqual(constraints['reference_ppt']['max_pages'], 202)
        self.assertEqual(constraints['pdf']['allowed_ext'], ['pdf'])
        self.assertEqual(constraints['reference_ppt']['allowed_ext'], ['ppt', 'pptx'])

    def test_create_upload_slot_rejects_oversized_pdf(self) -> None:
        with self._make_session() as db:
            user = self._create_user(db)
            with patch('app.services.file_service.get_settings') as mock_settings:
                mock_settings.return_value.upload_pdf_max_file_size_mb = 1
                mock_settings.return_value.upload_reference_ppt_max_file_size_mb = 100
                mock_settings.return_value.storage_provider = 'local'
                mock_settings.return_value.upload_subdir = 'uploads'

                with self.assertRaises(AppException) as ctx:
                    create_upload_slot(
                        db,
                        user_id=user.id,
                        filename='demo.pdf',
                        file_role='pdf_source',
                        content_type='application/pdf',
                        file_size=2 * 1024 * 1024,
                        base_url='http://testserver',
                    )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.code, 1001)
            self.assertIn('file size exceeds limit', ctx.exception.message)


if __name__ == '__main__':
    unittest.main()
