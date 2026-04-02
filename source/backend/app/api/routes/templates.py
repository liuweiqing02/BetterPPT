from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import CurrentUser
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.services.template_asset_service import assetize_template_file, get_template_assets

router = APIRouter(prefix='/templates', tags=['templates'])


@router.post('/{file_id}/assetize', response_model=APIResponse)
def post_template_assetize(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    data = assetize_template_file(db, user_id=current_user.id, file_id=file_id)
    return APIResponse(data=data)


@router.get('/{file_id}/assets', response_model=APIResponse)
def get_template_assets_view(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    data = get_template_assets(db, user_id=current_user.id, file_id=file_id)
    return APIResponse(data=data)
