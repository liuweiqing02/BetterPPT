from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import File, TemplatePageSchema, TemplateProfile, TemplateSlotDefinition, User

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICES_DIR = _REPO_ROOT / 'source' / 'backend' / 'app' / 'services'
if 'app.services' not in sys.modules:
    services_pkg = ModuleType('app.services')
    services_pkg.__path__ = [str(_SERVICES_DIR)]  # type: ignore[attr-defined]
    sys.modules['app.services'] = services_pkg

from app.services.template_asset_service import assetize_template_file, get_template_assets


def _find_reference_pptx() -> Path:
    root = Path(__file__).resolve().parents[4]
    candidates = sorted((root / 'ref').glob('*.pptx'))
    if not candidates:
        raise AssertionError('no reference pptx found under ref/')
    return candidates[0]


class TemplateAssetServiceTestCase(unittest.TestCase):
    def _make_session(self) -> Session:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        db_path = Path(tmpdir.name) / 'template_asset_service.sqlite3'
        engine = create_engine(
            f'sqlite+pysqlite:///{db_path.as_posix()}',
            connect_args={'check_same_thread': False},
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
        return session_factory()

    def _create_reference_file(self, db: Session, user_id: int) -> File:
        reference_path = _find_reference_pptx()
        reference_file = File(
            user_id=user_id,
            file_role='ppt_reference',
            storage_provider='local',
            storage_path=str(reference_path.resolve()),
            filename=reference_path.name,
            ext='pptx',
            mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            file_size=reference_path.stat().st_size,
            checksum_sha256=None,
            status='uploaded',
        )
        db.add(reference_file)
        db.commit()
        db.refresh(reference_file)
        return reference_file

    def _create_user(self, db: Session, username: str = 'asset-user') -> User:
        user = User(username=username, email=f'{username}@example.com', password_hash='x', status=1)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def test_assetize_template_creates_profile_pages_and_slots(self) -> None:
        with self._make_session() as db:
            user = self._create_user(db)
            reference_file = self._create_reference_file(db, user.id)

            data = assetize_template_file(db, user_id=user.id, file_id=reference_file.id)

            self.assertEqual(data.file_id, reference_file.id)
            self.assertGreater(data.asset_pages_count, 0)
            self.assertGreater(data.slots_count, 0)
            self.assertEqual(len(data.pages), data.asset_pages_count)
            self.assertEqual(len(data.slots), data.slots_count)

            profile = db.scalar(select(TemplateProfile).where(TemplateProfile.id == data.profile.id))
            self.assertIsNotNone(profile)
            pages = list(
                db.scalars(
                    select(TemplatePageSchema)
                    .where(TemplatePageSchema.template_profile_id == data.profile.id)
                    .order_by(TemplatePageSchema.page_no.asc())
                ).all()
            )
            slots = list(
                db.scalars(
                    select(TemplateSlotDefinition)
                    .where(TemplateSlotDefinition.template_profile_id == data.profile.id)
                    .order_by(TemplateSlotDefinition.page_no.asc(), TemplateSlotDefinition.z_index.asc())
                ).all()
            )

            self.assertEqual(len(pages), data.asset_pages_count)
            self.assertGreater(len(slots), 0)
            self.assertTrue(all(slot.slot_key for slot in slots))

    def test_get_template_assets_backfills_missing_slots(self) -> None:
        with self._make_session() as db:
            user = self._create_user(db, username='asset-user-backfill')
            reference_file = self._create_reference_file(db, user.id)

            assetize_template_file(db, user_id=user.id, file_id=reference_file.id)
            profile = db.scalar(select(TemplateProfile).where(TemplateProfile.file_id == reference_file.id))
            assert profile is not None

            db.execute(delete(TemplateSlotDefinition).where(TemplateSlotDefinition.template_profile_id == profile.id))
            db.commit()

            data = get_template_assets(db, user_id=user.id, file_id=reference_file.id)

            self.assertEqual(data.file_id, reference_file.id)
            self.assertGreater(data.asset_pages_count, 0)
            self.assertGreater(data.slots_count, 0)

            restored_slots = list(
                db.scalars(
                    select(TemplateSlotDefinition)
                    .where(TemplateSlotDefinition.template_profile_id == profile.id)
                    .order_by(TemplateSlotDefinition.page_no.asc(), TemplateSlotDefinition.z_index.asc())
                ).all()
            )
            self.assertGreater(len(restored_slots), 0)


if __name__ == '__main__':
    unittest.main()
