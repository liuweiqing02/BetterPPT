from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import CurrentUser
from app.db.session import get_db
from app.services.user_service import get_or_create_user


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: int | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    settings = get_settings()
    user_id = settings.auth_default_user_id

    if x_user_id:
        user_id = x_user_id
    elif authorization and authorization.lower().startswith('bearer '):
        token = authorization[7:].strip()
        if token.startswith('dev-user-'):
            suffix = token.removeprefix('dev-user-')
            if suffix.isdigit():
                user_id = int(suffix)

    user = get_or_create_user(db, user_id=user_id)
    return CurrentUser(id=user.id, username=user.username)
