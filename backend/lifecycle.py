"""SCRUM-102: an event's life after approval, and cancelling it. SCRUM-87: its review flags.

  POST /events/{id}/start-planning       Approved -> Planning (assigned Coordinator)
  POST /events/{id}/submit-safety        Planning -> Safety Review, once the gate is met
  POST /events/{id}/complete             Confirmed -> Completed, after the event's end
  POST /events/{id}/cancel               Cancelled, with a required reason
  GET  /events/{id}/lifecycle-actions    what the signed-in person may do, and why not
  GET  /events/{id}/review-flags         the event's active review flags
  POST /events/{id}/review-flags/clear   mark them as looked at

Who may make each move is in status_rules.py. This module adds what the move needs
around it: the gate, the end time, releasing bookings, and the notices (SCRUM-66).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from . import arrangements, booking_rules, event_hooks
from .auth import get_current_user
from .database import get_db
from .events import event_to_dict
from .models import ArrangementReviewFlag, EquipmentRequest, Event, User, VenueBooking
from .notifications import can_see_event, notify
from .status import APPROVED, CANCELLED, COMPLETED, CONFIRMED, PLANNING, SAFETY_REVIEW
from .status_rules import StatusChangeError, change_status, check_transition

router = APIRouter(prefix="/events", tags=["event lifecycle"])

KIND_EVENT_CANCELLED = "event_cancelled"  # [SCRUM-66]
BOOKING_CANCELLED = "Cancelled"


class CancelAction(BaseModel):
    """A cancellation must always say why."""

    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("A cancellation reason is required")
        return v.strip()


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


def get_event_or_404(event_id: int, db: Session) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def check_move(event: Event, new_status: str, actor: User) -> None:
    """The rulebook's answer as an HTTP error, for checking before a gate."""
    try:
        check_transition(event, new_status, actor)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))


def tell_organiser_and_coordinator(db: Session, event: Event, kind: str, message: str) -> None:
    """[SCRUM-66] Notify whichever of them exist and may still see the event, so a
    Draft with no Coordinator yet can still be cancelled."""
    for person in (event.organiser, event.assigned_coordinator):
        if person is not None and can_see_event(person, event):
            notify(db, person, event, kind, message)


def _result(event: Event, message: str) -> dict:
    data = event_to_dict(event)
    data["message"] = message
    return data


@router.post("/{event_id}/start-planning")
def start_planning(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The assigned Coordinator starts arranging an Approved event."""
    event = get_event_or_404(event_id, db)
    check_move(event, PLANNING, current_user)
    change_status(db, event, PLANNING, current_user)
    db.commit()
    db.refresh(event)
    return _result(event, "Planning started")


def _whats_missing_for_safety(db: Session, event: Event) -> List[str]:
    """The gate. Each sentence is one thing the Coordinator still has to do."""
    missing = []
    if not any(booking.status == "Confirmed" for booking in event.venue_bookings):
        missing.append("It needs at least one Confirmed venue booking.")
    if not arrangements.all_equipment_arranged(db, event):
        requests = db.query(EquipmentRequest).filter(EquipmentRequest.event_id == event.id).all()
        names = [
            request.equipment_type
            for request in requests
            if arrangements.latest_equipment_status(db, request) != arrangements.EQUIPMENT_ARRANGED
        ]
        missing.append(f"This equipment isn't arranged yet: {', '.join(names)}.")
    return missing


@router.post("/{event_id}/submit-safety")
def submit_for_safety_check(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send the event to the Operational Safety Check once its arrangements are ready.

    The person and status are checked before the gate, so someone who can't make
    this move isn't told what the event is missing. Submitting clears active review
    flags, because resubmitting says they have been looked at (the rows stay).
    """
    event = get_event_or_404(event_id, db)
    check_move(event, SAFETY_REVIEW, current_user)

    missing = _whats_missing_for_safety(db, event)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This event can't go to the safety check yet. {' '.join(missing)}",
        )

    change_status(db, event, SAFETY_REVIEW, current_user)
    arrangements.clear_review_flags(db, event, current_user)
    db.commit()
    db.refresh(event)
    return _result(event, "Sent to the safety check")


def _why_not_ended(event: Event) -> Optional[str]:
    """None once the event's end time has come, otherwise a sentence saying when it will."""
    end = arrangements.event_end(event)
    if end is not None and booking_rules.utc_now() >= end:
        return None
    ends = f" It ends at {booking_rules.singapore_time(end).strftime('%Y-%m-%d %H:%M')} Singapore time." if end else ""
    return f"This event hasn't ended yet, so it can't be marked Completed.{ends}"


@router.post("/{event_id}/complete")
def complete_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a Confirmed event Completed. Refused before its end time."""
    event = get_event_or_404(event_id, db)
    check_move(event, COMPLETED, current_user)

    not_ended = _why_not_ended(event)
    if not_ended:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=not_ended)

    change_status(db, event, COMPLETED, current_user)
    db.commit()
    db.refresh(event)
    return _result(event, "Event completed")


def release_bookings(event: Event) -> None:
    """Every booking that still holds a venue becomes Cancelled, and the row stays.

    A hold is a Pending booking and the hold checks only look at Pending, so this
    also stops its warning and expiry notices. It tests the status rather than
    is_blocking on purpose: a hold past its expiry still reads Pending until the
    next check, which would then tell the Coordinator to rebook a cancelled event.
    """
    for booking in event.venue_bookings:
        if booking.status in booking_rules.BLOCKING_STATUSES:
            booking.status = BOOKING_CANCELLED


@router.post("/{event_id}/cancel")
def cancel_event(
    event_id: int,
    action: CancelAction,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel the event, release what it holds and tell the people concerned.

    One commit at the end, so all of it is saved or none of it. Equipment
    reservations and Attendee notices arrive through the event_cancelled hook.
    """
    event = get_event_or_404(event_id, db)
    try:
        change_status(db, event, CANCELLED, current_user, reason=action.reason)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    release_bookings(event)
    event_hooks.event_cancelled(db, event, current_user, action.reason)
    tell_organiser_and_coordinator(
        db, event, KIND_EVENT_CANCELLED,
        f'The event "{event.title}" has been cancelled. Reason: {action.reason}',
    )
    db.commit()
    db.refresh(event)
    return _result(event, "Event cancelled")


def _may_move(event: Event, new_status: str, user: User) -> bool:
    try:
        check_transition(event, new_status, user)
    except StatusChangeError:
        return False
    return True


def _action(action: str, label: str, why: Optional[str] = None) -> dict:
    return {"action": action, "label": label, "available": why is None, "why": why}


@router.get("/{event_id}/lifecycle-actions")
def lifecycle_actions(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What the event detail dialog offers this person. Added beyond the agreed routes
    because the dialog can't work out equipment status or the end time itself, and it
    must say why a button is unavailable. The rulebook decides; nothing is repeated."""
    event = get_event_or_404(event_id, db)
    role = _role_of(current_user)
    own_request = role == "organiser" and event.organiser_id == current_user.id
    if not own_request and role not in ("coordinator", "lead"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can't view this event's actions")

    actions = []
    if _may_move(event, PLANNING, current_user):
        actions.append(_action("start-planning", "Start planning"))
    if _may_move(event, SAFETY_REVIEW, current_user):
        missing = _whats_missing_for_safety(db, event)
        actions.append(_action("submit-safety", "Submit for safety check", " ".join(missing) or None))
    if _may_move(event, COMPLETED, current_user):
        actions.append(_action("complete", "Mark completed", _why_not_ended(event)))
    if _may_move(event, CANCELLED, current_user):
        actions.append(_action("cancel", "Cancel event"))
    elif own_request and event.status in (APPROVED, PLANNING, SAFETY_REVIEW, CONFIRMED):
        actions.append(_action("cancel", "Cancel event", "After approval, only the assigned Coordinator or a Lead can cancel this event."))
    return actions


# --- [SCRUM-87] Review flags --------------------------------------------------------


def _can_view_flags(user: User, event: Event) -> bool:
    role = _role_of(user)
    if role == "organiser":
        return event.organiser_id == user.id
    return _can_clear_flags(user, event)


def _can_clear_flags(user: User, event: Event) -> bool:
    role = _role_of(user)
    return role == "lead" or (role == "coordinator" and event.assigned_coordinator_id == user.id)


def _flag_to_dict(db: Session, flag: ArrangementReviewFlag) -> dict:
    """One flag, naming the arrangement it marks."""
    if flag.booking_id is not None:
        booking = db.get(VenueBooking, flag.booking_id)
        kind, label = "venue", booking.venue.name
    else:
        request = db.get(EquipmentRequest, flag.equipment_request_id)
        kind, label = "equipment", f"{request.equipment_type} (quantity {request.quantity})"
    creator = db.get(User, flag.created_by_id)
    return {
        "id": flag.id,
        "kind": kind,
        "label": label,
        "booking_id": flag.booking_id,
        "equipment_request_id": flag.equipment_request_id,
        "source": flag.source,
        "reason": flag.reason,
        "created_by": creator.username if creator else None,
        "created_at": flag.created_at,
    }


@router.get("/{event_id}/review-flags")
def list_review_flags(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The arrangements marked for another look: for the event's Organiser, its assigned Coordinator and the Lead."""
    event = get_event_or_404(event_id, db)
    if not _can_view_flags(current_user, event):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can't view this event's review flags")
    return [_flag_to_dict(db, flag) for flag in arrangements.active_review_flags(db, event)]


@router.post("/{event_id}/review-flags/clear")
def clear_event_review_flags(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The assigned Coordinator or the Lead says the flags have been looked at. The rows stay."""
    event = get_event_or_404(event_id, db)
    if not _can_clear_flags(current_user, event):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event's assigned Coordinator or a Lead can clear its review flags",
        )
    cleared = arrangements.clear_review_flags(db, event, current_user)
    db.commit()
    return {"message": "Review flags cleared", "cleared": cleared}
