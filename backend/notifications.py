"""SCRUM-51: Notify the Organiser of submission and review outcome.

Acceptance criteria covered:
  AC1  The Organiser is notified when their request is successfully submitted.
  AC2  The Organiser is notified when a Coordinator returns the request for
       clarification, including the Coordinator's note.
  AC3  The Organiser is notified when the request is approved or rejected,
       and the notification says which outcome it was.
  AC4  A user only receives notifications for events they are entitled to see.
  AC5  (Assumption) Delivery channel is the team's choice. We chose in-app:
       a notification is a row the user reads through GET /notifications.

How it fits together:
  * ``notify`` is the one function every story calls to send a notification.
    It refuses to notify anyone who is not entitled to see the event (AC4),
    so a caller cannot leak an event by mistake. SCRUM-63 reuses it.
  * ``notify_organiser_of_status`` holds the SCRUM-51 wording for each status.
    The event endpoints call it only after the status change has passed its
    checks, so a refused action never sends a notification.
  * ``notify`` does not commit. The endpoint commits, so the status change and
    its notification are saved together or not at all - the same pattern as
    ``change_status`` in status_rules.py.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Event, Notification, User
from .status import APPROVED, REJECTED, RETURNED, SUBMITTED

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Notification kinds. Stored on each row so a frontend can show an icon or
# filter without parsing the message text.
KIND_SUBMITTED = "submitted"
KIND_RETURNED = "returned"
KIND_APPROVED = "approved"
KIND_REJECTED = "rejected"


class NotEntitled(Exception):
    """The intended recipient is not allowed to see this event."""


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


def can_see_event(user: User, event: Event) -> bool:
    """AC4: who is entitled to see an event, for notification purposes.

    Mirrors the visibility already used by GET /events:
      * an Organiser sees only events they organise
      * a Coordinator sees only events assigned to them
    Every other role receives no event notifications yet. Widen this when a
    later story (e.g. the Event Coordinator Lead, Week 7 Change #5) needs it.
    """
    role = _role_of(user)
    if role == "organiser":
        return event.organiser_id == user.id
    if role == "coordinator":
        return event.assigned_coordinator_id == user.id
    return False


def notify(
    db: Session,
    recipient: User,
    event: Event,
    kind: str,
    message: str,
) -> Notification:
    """Queue one notification for ``recipient`` about ``event``.

    Raises ``NotEntitled`` instead of writing anything if the recipient may not
    see the event. The caller commits.
    """
    if not can_see_event(recipient, event):
        raise NotEntitled(
            f"User {recipient.id} is not entitled to see event {event.id}"
        )

    notification = Notification(
        user_id=recipient.id,
        event_id=event.id,
        kind=kind,
        message=message,
    )
    db.add(notification)
    return notification


def notify_organiser_of_status(
    db: Session,
    event: Event,
    new_status: str,
    note: Optional[str] = None,
) -> Optional[Notification]:
    """Send the Organiser the SCRUM-51 notification for ``new_status``.

    Returns None for statuses SCRUM-51 does not cover (e.g. Under Review).
    """
    organiser = event.organiser
    if organiser is None:
        organiser = db.get(User, event.organiser_id)
    title = event.title

    if new_status == SUBMITTED:
        kind = KIND_SUBMITTED
        message = f'Your event request "{title}" has been submitted.'
    elif new_status == RETURNED:
        kind = KIND_RETURNED
        message = (
            f'Your event request "{title}" was returned for clarification. '
            f"Coordinator's note: {note}"
        )
    elif new_status == APPROVED:
        kind = KIND_APPROVED
        message = f'Your event request "{title}" has been approved.'
    elif new_status == REJECTED:
        kind = KIND_REJECTED
        message = f'Your event request "{title}" has been rejected.'
        if note:
            message += f" Reason: {note}"
    else:
        return None

    return notify(db, organiser, event, kind, message)


def _to_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "event_id": n.event_id,
        "kind": n.kind,
        "message": n.message,
        "created_at": n.created_at,
        "read": n.read_at is not None,
        "read_at": n.read_at,
    }


@router.get("")
def list_my_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The current user's notifications, newest first.

    AC4, second line of defence: a notification about an event the user can
    no longer see (for example after the event is reassigned to another
    Coordinator) is left out.
    """
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .all()
    )
    visible = [n for n in rows if n.event is None or can_see_event(current_user, n.event)]
    return [_to_dict(n) for n in visible]


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark one of your own notifications as read.

    Someone else's notification answers 404, not 403, so its existence is
    not revealed.
    """
    n = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )
    if n is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
        db.refresh(n)
    return _to_dict(n)
