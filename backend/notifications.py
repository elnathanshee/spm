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
from .models import Event, Notification, User, VenueBooking
from .status import APPROVED, REJECTED, RETURNED, SUBMITTED

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Notification kinds. Stored on each row so a frontend can show an icon or
# filter without parsing the message text.
KIND_SUBMITTED = "submitted"
KIND_RETURNED = "returned"
KIND_APPROVED = "approved"
KIND_REJECTED = "rejected"
# [SCRUM-23 | Renee] reassignment kinds
KIND_REASSIGNED_TO_YOU = "reassigned_to_you"
KIND_REASSIGNED_AWAY = "reassigned_away"
KIND_COORDINATOR_CHANGED = "coordinator_changed"
# [SCRUM-63 | Renee] first-assignment kinds
KIND_COORDINATOR_ASSIGNED = "coordinator_assigned"
KIND_ASSIGNED_TO_YOU = "assigned_to_you"


class NotEntitled(Exception):
    """The intended recipient is not allowed to see this event."""


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


def can_see_event(user: User, event: Event) -> bool:
    """AC4: who is entitled to see an event, for notification purposes.

    Mirrors the visibility already used by GET /events:
      * an Organiser sees only events they organise
      * a Coordinator sees only events assigned to them
      * the Event Coordinator Lead sees every event (SCRUM-23)
    Every other role receives no event notifications yet.
    """
    role = _role_of(user)
    if role == "organiser":
        return event.organiser_id == user.id
    if role == "coordinator":
        return event.assigned_coordinator_id == user.id
    if role == "lead":  # [SCRUM-23 | Renee]
        return True
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
        "booking_id": n.booking_id,
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
    visible = [n for n in rows if can_see_notification(current_user, n)]
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


# [SCRUM-23 | Renee] NEW: the one notification that outlives the recipient's
# access. A Coordinator who is reassigned away must still be told, but can no
# longer see the event. So entitlement is checked now, while they still are the
# assigned Coordinator, and the row is stored without an event link: it stays
# in their list (GET /notifications keeps rows with no event) but points them
# at nothing they can no longer open. Call this BEFORE changing the assignment.
def notify_former_coordinator(
    db: Session,
    former: User,
    event: Event,
    message: str,
) -> Notification:
    if not can_see_event(former, event):
        raise NotEntitled(
            f"User {former.id} is not the assigned coordinator of event {event.id}"
        )
    notification = Notification(
        user_id=former.id,
        event_id=None,
        kind=KIND_REASSIGNED_AWAY,
        message=message,
    )
    db.add(notification)
    return notification


# [SCRUM-63 | Renee] NEW: notify on a Coordinator's FIRST assignment.
#   AC1  The Organiser is told a Coordinator was assigned, naming them.
#   AC3  The Coordinator is told the event is now theirs.
#   AC2 (a change of Coordinator) is handled by SCRUM-23's reassignment,
#   which sends KIND_COORDINATOR_CHANGED to the Organiser.
# Call AFTER the assignment is set, so both recipients pass the entitlement
# check in notify(). The caller commits.
def notify_first_assignment(db: Session, event: Event) -> None:
    organiser = event.organiser or db.get(User, event.organiser_id)
    coordinator = event.assigned_coordinator or db.get(User, event.assigned_coordinator_id)
    notify(
        db,
        organiser,
        event,
        KIND_COORDINATOR_ASSIGNED,
        f'Coordinator {coordinator.username} has been assigned to your event '
        f'request "{event.title}".',
    )
    notify(
        db,
        coordinator,
        event,
        KIND_ASSIGNED_TO_YOU,
        f'Event request "{event.title}" has been assigned to you.',
    )


KIND_BOOKING_SUBMITTED = "booking_submitted"
KIND_BOOKING_APPROVED = "booking_approved"
KIND_BOOKING_REJECTED = "booking_rejected"
BOOKING_KINDS = {KIND_BOOKING_SUBMITTED, KIND_BOOKING_APPROVED, KIND_BOOKING_REJECTED}


def can_see_notification(user: User, notification: Notification) -> bool:
    if notification.booking_id is not None and notification.kind in BOOKING_KINDS:
        booking = notification.booking
        if booking is None or notification.user_id != user.id:
            return False
        if notification.kind == KIND_BOOKING_SUBMITTED:
            return _role_of(user) == "venue_staff"
        return booking.requester_id == user.id
    return notification.event is None or can_see_event(user, notification.event)


def notify_booking(db: Session, booking: VenueBooking, kind: str) -> None:
    """Queue booking messages in the caller's transaction; uniqueness is DB enforced.

    Historical bookings with no recorded requester produce no outcome message.
    Do not infer their requester from the event's mutable assignment.
    """
    if kind not in BOOKING_KINDS:
        raise ValueError("Unknown booking notification kind")
    if kind == KIND_BOOKING_SUBMITTED:
        recipients = [u for u in db.query(User).all() if _role_of(u) == "venue_staff"]
        outcome = "was submitted"
    else:
        requester = db.get(User, booking.requester_id) if booking.requester_id else None
        recipients = [requester] if requester else []
        outcome = "was approved" if kind == KIND_BOOKING_APPROVED else "was rejected"
    message = (
        f'Booking for "{booking.venue.name}" from '
        f'{booking.booking_start.isoformat(sep=" ")} to '
        f'{booking.booking_end.isoformat(sep=" ")} {outcome}.'
    )
    if kind == KIND_BOOKING_REJECTED:
        reason = (booking.decision.reason if booking.decision else None) or booking.rejection_reason
        if reason:
            message += f" Reason: {reason}"
    for recipient in recipients:
        # Flush also makes repeated helper calls in one transaction observable.
        db.flush()
        exists = db.query(Notification.id).filter_by(
            booking_id=booking.id, kind=kind, user_id=recipient.id
        ).first()
        if exists is None:
            db.add(Notification(user_id=recipient.id, event_id=booking.event_id,
                                booking_id=booking.id, kind=kind, message=message))
