from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_or_create_user(db: Session, user_id: int, username: str | None = None) -> User:
    user = db.get(User, user_id)
    if user:
        return user

    user = User(id=user_id, username=username or f'user_{user_id}')
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))
