from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import CurrentUser
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.metrics import MetricsOverviewData
from app.services.metrics_service import get_metrics_overview

router = APIRouter(prefix='/metrics', tags=['metrics'])


@router.get('/overview', response_model=APIResponse)
def get_metrics_overview_api(
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> APIResponse:
    data = get_metrics_overview(db, user_id=current_user.id, days=days)
    return APIResponse(data=MetricsOverviewData.model_validate(data))
