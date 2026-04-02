from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.constants import FileRole
from app.core.errors import AppException
from app.models import File, TemplatePageSchema, TemplateProfile, TemplateSlotDefinition
from app.schemas.template_asset import (
    TemplateAssetPageData,
    TemplateAssetProfileData,
    TemplateAssetSlotData,
    TemplateAssetsData,
)
from app.services.template_service import analyze_and_persist_template


def _get_reference_file(db: Session, *, user_id: int, file_id: int) -> File:
    file = db.get(File, file_id)
    if not file or file.user_id != user_id:
        raise AppException(status_code=404, code=1002, message='reference file not found')
    if file.file_role != FileRole.PPT_REFERENCE:
        raise AppException(status_code=400, code=1001, message='reference file must be ppt_reference')
    return file


def _normalize_layout_slots(layout_schema_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    layout_schema_json = layout_schema_json if isinstance(layout_schema_json, dict) else {}
    raw_slots = layout_schema_json.get('slots') or []
    normalized: list[dict[str, Any]] = []
    for index, raw_slot in enumerate(raw_slots, start=1):
        if isinstance(raw_slot, dict):
            slot_key = str(raw_slot.get('slot_key') or raw_slot.get('name') or f'slot_{index}')
            slot_type = str(raw_slot.get('slot_type') or _infer_slot_type(slot_key))
            slot_role = str(raw_slot.get('slot_role') or _infer_slot_role(slot_key))
        else:
            slot_key = str(raw_slot or f'slot_{index}')
            slot_type = _infer_slot_type(slot_key)
            slot_role = _infer_slot_role(slot_key)
        normalized.append(
            {
                'slot_key': slot_key,
                'slot_type': slot_type,
                'slot_role': slot_role,
                'slot_index': index,
            }
        )
    if normalized:
        return normalized
    return [
        {
            'slot_key': 'title',
            'slot_type': 'text',
            'slot_role': 'title',
            'slot_index': 1,
        },
        {
            'slot_key': 'body',
            'slot_type': 'text',
            'slot_role': 'bullet',
            'slot_index': 2,
        },
    ]


def _infer_slot_type(slot_key: str) -> str:
    value = str(slot_key or '').lower()
    if any(token in value for token in ('image', 'visual', 'hero', 'figure', 'photo', 'chart')):
        return 'image'
    if any(token in value for token in ('table', 'datatable', 'grid')):
        return 'table'
    return 'text'


def _infer_slot_role(slot_key: str) -> str:
    value = str(slot_key or '').lower()
    if 'title' in value:
        return 'title'
    if 'subtitle' in value:
        return 'subtitle'
    if any(token in value for token in ('bullet', 'body', 'points', 'summary', 'agenda', 'list')):
        return 'bullet'
    if any(token in value for token in ('table', 'datatable', 'grid')):
        return 'datatable'
    if any(token in value for token in ('image', 'visual', 'hero', 'figure', 'photo', 'chart')):
        return 'figure'
    return 'summary'


def _slot_bbox(slot_index: int, slot_type: str) -> tuple[float, float, float, float]:
    if slot_type == 'image':
        width = 0.36
        height = 0.28
    elif slot_type == 'table':
        width = 0.52
        height = 0.24
    else:
        width = 0.46
        height = 0.16 if slot_index == 1 else 0.22
    column = 0 if slot_index % 2 else 1
    row = max(0, (slot_index - 1) // 2)
    x = 0.08 + column * 0.42
    y = 0.14 + row * 0.20
    return (round(x, 4), round(y, 4), round(width, 4), round(height, 4))


def _get_latest_template_profile(db: Session, *, file_id: int) -> TemplateProfile | None:
    stmt = (
        select(TemplateProfile)
        .where(TemplateProfile.file_id == file_id)
        .order_by(TemplateProfile.updated_at.desc(), TemplateProfile.id.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def _load_template_pages(db: Session, *, profile_id: int) -> list[TemplatePageSchema]:
    stmt = (
        select(TemplatePageSchema)
        .where(TemplatePageSchema.template_profile_id == profile_id)
        .order_by(TemplatePageSchema.page_no.asc())
    )
    return list(db.scalars(stmt).all())


def _load_template_slots(db: Session, *, profile_id: int) -> list[TemplateSlotDefinition]:
    stmt = (
        select(TemplateSlotDefinition)
        .where(TemplateSlotDefinition.template_profile_id == profile_id)
        .order_by(TemplateSlotDefinition.page_no.asc(), TemplateSlotDefinition.z_index.asc(), TemplateSlotDefinition.slot_key.asc())
    )
    return list(db.scalars(stmt).all())


def _upsert_template_slot_definitions(db: Session, *, profile_id: int, pages: list[TemplatePageSchema]) -> int:
    db.execute(delete(TemplateSlotDefinition).where(TemplateSlotDefinition.template_profile_id == profile_id))
    inserted = 0
    for page in pages:
        for slot in _normalize_layout_slots(page.layout_schema_json):
            bbox_x, bbox_y, bbox_w, bbox_h = _slot_bbox(int(slot['slot_index']), str(slot['slot_type']))
            db.add(
                TemplateSlotDefinition(
                    template_profile_id=profile_id,
                    page_no=int(page.page_no),
                    slot_key=str(slot['slot_key']),
                    slot_type=str(slot['slot_type']),
                    slot_role=str(slot['slot_role']),
                    bbox_x=bbox_x,
                    bbox_y=bbox_y,
                    bbox_w=bbox_w,
                    bbox_h=bbox_h,
                    z_index=int(slot['slot_index']),
                    style_tokens_json=page.style_tokens_json or {},
                    constraints_json={'derived_from': 'layout_schema_json'},
                )
            )
            inserted += 1
    db.flush()
    return inserted


def ensure_template_slot_definitions(db: Session, *, profile_id: int) -> int:
    pages = _load_template_pages(db, profile_id=profile_id)
    if not pages:
        return 0
    return _upsert_template_slot_definitions(db, profile_id=profile_id, pages=pages)


def _build_assets_data(db: Session, *, profile: TemplateProfile) -> TemplateAssetsData:
    pages = _load_template_pages(db, profile_id=profile.id)
    slots = _load_template_slots(db, profile_id=profile.id)

    if not slots and pages:
        ensure_template_slot_definitions(db, profile_id=profile.id)
        db.commit()
        slots = _load_template_slots(db, profile_id=profile.id)

    return TemplateAssetsData(
        file_id=profile.file_id,
        profile=TemplateAssetProfileData.model_validate(profile),
        pages=[TemplateAssetPageData.model_validate(page) for page in pages],
        slots=[TemplateAssetSlotData.model_validate(slot) for slot in slots],
        asset_pages_count=len(pages),
        slots_count=len(slots),
    )


def assetize_template_file(
    db: Session,
    *,
    user_id: int,
    file_id: int,
    detail_level: str = 'balanced',
) -> TemplateAssetsData:
    reference_file = _get_reference_file(db, user_id=user_id, file_id=file_id)
    task_no = f'assetize-file-{file_id}'
    result = analyze_and_persist_template(db, reference_file, detail_level, task_no)
    profile_id = int(result.get('profile_id') or 0)
    if not profile_id:
        raise AppException(status_code=404, code=1002, message='template profile not found')

    ensure_template_slot_definitions(db, profile_id=profile_id)
    db.commit()

    profile = db.get(TemplateProfile, profile_id)
    if not profile:
        raise AppException(status_code=404, code=1002, message='template profile not found')
    return _build_assets_data(db, profile=profile)


def get_template_assets(
    db: Session,
    *,
    user_id: int,
    file_id: int,
) -> TemplateAssetsData:
    reference_file = _get_reference_file(db, user_id=user_id, file_id=file_id)
    profile = _get_latest_template_profile(db, file_id=reference_file.id)
    if not profile:
        raise AppException(status_code=404, code=1002, message='template assets not found')
    return _build_assets_data(db, profile=profile)
