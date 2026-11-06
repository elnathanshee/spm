"""[Sprint 5] What several stories share about an event's arrangements.

Three things live here because more than one story needs them:

  * Equipment request status (SCRUM-40). Technical Support update it, and
    SCRUM-102's gate reads it before an event goes to the safety check.
  * The event's time window (SCRUM-43, SCRUM-102). Equipment availability uses
    it, and an event can only be completed after it ends.
  * Review flags (SCRUM-87, SCRUM-50). A safety decision or an approved important
    change marks the event's bookings and equipment for another look. They are
    flagged, never cancelled or deleted, and a cleared flag stays as history.

Nothing here commits: the caller does, so everything is saved together.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from .booking_rules import is_blocking, occupied_window
from .models import ArrangementReviewFlag, EquipmentRequest, EquipmentRequestUpdate, Event, User

EQUIPMENT_REQUESTED = "requested"
EQUIPMENT_RESERVED = "reserved"
EQUIPMENT_ARRANGED = "arranged"
EQUIPMENT_UNAVAILABLE = "unavailable"
EQUIPMENT_STATUSES = (EQUIPMENT_REQUESTED, EQUIPMENT_RESERVED, EQUIPMENT_ARRANGED, EQUIPMENT_UNAVAILABLE)

# events.proposed_start holds Singapore wall time; booking times are stored in UTC.
SINGAPORE_OFFSET = timedelta(hours=8)

SOURCE_SAFETY_REVIEW = "safety_review"
SOURCE_IMPORTANT_CHANGE = "important_change"


def equipment_status_history(db: Session, request: EquipmentRequest) -> List[EquipmentRequestUpdate]:
    """Every status update on this request, oldest first."""
    return (
        db.query(EquipmentRequestUpdate)
        .filter(EquipmentRequestUpdate.request_id == request.id)
        .order_by(EquipmentRequestUpdate.id.asc())
        .all()
    )


def latest_equipment_status(db: Session, request: EquipmentRequest) -> str:
    """The request's current status: its latest update, or 'requested' if it has none."""
    latest = (
        db.query(EquipmentRequestUpdate)
        .filter(EquipmentRequestUpdate.request_id == request.id)
        .order_by(EquipmentRequestUpdate.id.desc())
        .first()
    )
    return latest.status if latest else EQUIPMENT_REQUESTED


def record_equipment_status(
    db: Session, request: EquipmentRequest, status: str, note: Optional[str], actor: User
) -> EquipmentRequestUpdate:
    """Add a status update. Refuses a status outside EQUIPMENT_STATUSES with ValueError."""
    if status not in EQUIPMENT_STATUSES:
        raise ValueError(f"Unknown equipment status '{status}'")
    update = EquipmentRequestUpdate(request_id=request.id, status=status, note=note, actor_id=actor.id)
    db.add(update)
    db.flush()
    return update


def all_equipment_arranged(db: Session, event: Event) -> bool:
    """SCRUM-102's gate: every equipment request is arranged. No requests counts as arranged."""
    requests = db.query(EquipmentRequest).filter(EquipmentRequest.event_id == event.id).all()
    return all(latest_equipment_status(db, request) == EQUIPMENT_ARRANGED for request in requests)


def _end_of_singapore_day(proposed_start: datetime) -> datetime:
    """The midnight after proposed_start (Singapore wall time), in UTC."""
    next_day = proposed_start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return next_day - SINGAPORE_OFFSET


def event_window(event: Event) -> Optional[Tuple[datetime, datetime]]:
    """When the event occupies its resources, as naive UTC.

    With bookings that hold a venue: from the earliest setup to the latest
    turnaround. Without any: from proposed_start to the end of that Singapore day,
    since a request has no end time of its own. None if it has neither.
    """
    windows = [
        occupied_window(booking.booking_start, booking.booking_end, booking.venue)
        for booking in event.venue_bookings
        if is_blocking(booking)
    ]
    if windows:
        return min(start for start, _ in windows), max(end for _, end in windows)
    if event.proposed_start is None:
        return None
    return event.proposed_start - SINGAPORE_OFFSET, _end_of_singapore_day(event.proposed_start)


def event_end(event: Event) -> Optional[datetime]:
    """When the event itself is over, as naive UTC: the end of its last booking
    that holds a venue, without turnaround, or the end of its proposed day."""
    ends = [booking.booking_end for booking in event.venue_bookings if is_blocking(booking)]
    if ends:
        return max(ends)
    if event.proposed_start is None:
        return None
    return _end_of_singapore_day(event.proposed_start)


def active_review_flags(db: Session, event: Event) -> List[ArrangementReviewFlag]:
    """The event's flags nobody has cleared yet, oldest first."""
    return (
        db.query(ArrangementReviewFlag)
        .filter(ArrangementReviewFlag.event_id == event.id, ArrangementReviewFlag.cleared_at.is_(None))
        .order_by(ArrangementReviewFlag.id.asc())
        .all()
    )


def flag_event_arrangements(
    db: Session, event: Event, source: str, reason: str, actor: User
) -> List[ArrangementReviewFlag]:
    """Mark every booking that holds a venue and every equipment request for review.

    An arrangement that already has an active flag from this source isn't flagged
    twice. Returns only the flags this call created. Nothing is cancelled or deleted.
    """
    active = {
        (flag.booking_id, flag.equipment_request_id)
        for flag in active_review_flags(db, event)
        if flag.source == source
    }
    targets = [(booking.id, None) for booking in event.venue_bookings if is_blocking(booking)]
    targets += [
        (None, request.id)
        for request in db.query(EquipmentRequest).filter(EquipmentRequest.event_id == event.id).all()
    ]
    created = []
    for booking_id, request_id in targets:
        if (booking_id, request_id) in active:
            continue
        flag = ArrangementReviewFlag(
            event_id=event.id,
            booking_id=booking_id,
            equipment_request_id=request_id,
            source=source,
            reason=reason,
            created_by_id=actor.id,
        )
        db.add(flag)
        created.append(flag)
    db.flush()
    return created


def clear_review_flags(db: Session, event: Event, actor: User) -> int:
    """Mark the event's active flags as reviewed. The rows stay as history. Returns how many."""
    flags = active_review_flags(db, event)
    now = datetime.utcnow()
    for flag in flags:
        flag.cleared_at = now
        flag.cleared_by_id = actor.id
    db.flush()
    return len(flags)
