from sqlalchemy.orm import Session

from app.models.task_event import TaskEvent


def add_task_event(
    db: Session,
    *,
    task_id: int,
    event_type: str,
    message: str | None = None,
    payload_json: dict | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task_id,
        event_type=event_type,
        message=message,
        payload_json=payload_json,
    )
    db.add(event)
    db.flush()
    return event
