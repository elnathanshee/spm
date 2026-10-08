from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Notification, User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def my_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.recipient_id == current_user.id).order_by(Notification.created_at.desc(), Notification.id.desc()).all()
    return [{"id": row.id, "event_id": row.event_id, "message": row.message, "created_at": row.created_at, "read": row.read} for row in rows]
