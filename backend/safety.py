"""SCRUM-86, SCRUM-87: the Operational Safety Check (Week 7 customer change 6).

  GET  /safety/events                events waiting for a safety check
  GET  /safety/events/{id}           what the Safety Officer needs to decide
  POST /safety/events/{id}/decision  approve, reject or request_changes, with a reason

Approval is the only way an event reaches Confirmed: status_rules.py has one route
into that status, and only the Safety Officer can take it. Reject and request changes
both return the event to Planning and mark its arrangements for review (SCRUM-87).
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from . import arrangements, booking_rules, event_hooks
from .auth import get_current_user
from .database import get_db
from .events import event_to_dict
from .lifecycle import tell_organiser_and_coordinator
from .models import EquipmentRequest, Event, SafetyCheck, User, VenueBooking
from .notifications import can_see_event, notify
from .status import CONFIRMED, PLANNING, SAFETY_REVIEW
from .status_rules import StatusChangeError, change_status
from .venues import deserialize_list

router = APIRouter(prefix="/safety", tags=["safety"])

APPROVE = "approve"
REJECT = "reject"
REQUEST_CHANGES = "request_changes"

KIND_EVENT_CONFIRMED = "event_confirmed"  # [SCRUM-66]
KIND_SAFETY_RETURNED = "safety_returned"  # [SCRUM-86]


class SafetyDecision(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    reason: Optional[str] = None

    @model_validator(mode="after")
    def reason_unless_approving(self):
        self.reason = (self.reason or "").strip() or None
        if self.decision != APPROVE and not self.reason:
            raise ValueError("A reason is required when rejecting or requesting changes")
        return self


def require_safety_officer(current_user: User = Depends(get_current_user)) -> User:
    if (current_user.role or "").strip().lower() not in ("safety_officer", "safety officer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only a Safety Officer can do this")
    return current_user


def _event_in_review(event_id: int, db: Session) -> Event:
    """404 for an event that isn't waiting: this role sees only events in Safety Review."""
    event = db.get(Event, event_id)
    if event is None or event.status != SAFETY_REVIEW:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This event isn't waiting for a safety check")
    return event


def _holding_bookings(event: Event) -> List[VenueBooking]:
    return [booking for booking in event.venue_bookings if booking_rules.is_blocking(booking)]


def _summary(event: Event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "status": event.status,
        "proposed_start": event.proposed_start,
        "expected_attendance": event.expected_attendance,
        "coordinator": event.assigned_coordinator.username if event.assigned_coordinator else None,
        "venues": sorted({booking.venue.name for booking in _holding_bookings(event)}),
    }


def _restrictions_during(booking: VenueBooking) -> List[dict]:
    """The venue's unavailable periods that touch this booking's occupied window."""
    start, end = booking_rules.occupied_window(booking.booking_start, booking.booking_end, booking.venue)
    return [
        {"start_at": period.start_at, "end_at": period.end_at, "reason": period.reason}
        for period in booking.venue.unavailable_periods
        if booking_rules.windows_overlap(start, end, period.start_at, period.end_at)
    ]


def _venue_details(booking: VenueBooking) -> dict:
    venue = booking.venue
    return {
        "booking_id": booking.id,
        "booking_start": booking.booking_start,
        "booking_end": booking.booking_end,
        "booking_status": booking.status,
        "name": venue.name,
        "location": venue.location,
        "capacity": venue.capacity,
        "supported_layouts": deserialize_list(venue.supported_layouts),
        "facilities": deserialize_list(venue.facilities),
        "accessibility": venue.accessibility,
        "operating_hours": venue.operating_hours,
        "setup_time_mins": venue.setup_time_mins,
        "turnaround_time_mins": venue.turnaround_time_mins,
        "unavailable_periods": _restrictions_during(booking),
    }


def _equipment_details(db: Session, event: Event) -> List[dict]:
    """technical_requirements is where placement notes are written; there's no column of their own."""
    requests = db.query(EquipmentRequest).filter(EquipmentRequest.event_id == event.id).order_by(EquipmentRequest.id).all()
    details = []
    for request in requests:
        updates = arrangements.equipment_status_history(db, request)
        details.append({
            "id": request.id,
            "equipment_type": request.equipment_type,
            "quantity": request.quantity,
            "technical_requirements": request.technical_requirements,
            "status": arrangements.latest_equipment_status(db, request),
            "status_note": updates[-1].note if updates else None,
        })
    return details


def _check_to_dict(check: SafetyCheck) -> dict:
    return {
        "id": check.id,
        "decision": check.decision,
        "reason": check.reason,
        "decided_by": check.decided_by.username,
        "decided_at": check.decided_at,
    }


@router.get("/events")
def list_events_in_safety_review(
    current_user: User = Depends(require_safety_officer),
    db: Session = Depends(get_db),
):
    """Events waiting for a check, soonest first. An event with no date goes last."""
    events = db.query(Event).filter(Event.status == SAFETY_REVIEW).all()
    events.sort(key=lambda e: (e.proposed_start is None, e.proposed_start or 0, e.id))
    return [_summary(event) for event in events]


@router.get("/events/{event_id}")
def safety_event_details(
    event_id: int,
    current_user: User = Depends(require_safety_officer),
    db: Session = Depends(get_db),
):
    """Attendance, venue capacity and layouts, accessibility needs, equipment with its
    notes, the venue's known restrictions, and any earlier checks of this event."""
    event = _event_in_review(event_id, db)
    data = _summary(event)
    data.update({
        "accessibility_needs": event.accessibility_needs,
        "venue_requirements": event.venue_requirements,
        "equipment_requirements": event.equipment_requirements,
        "venue_details": [_venue_details(booking) for booking in _holding_bookings(event)],
        "equipment": _equipment_details(db, event),
        "checks": [_check_to_dict(check) for check in sorted(event.safety_checks, key=lambda c: c.id)],
    })
    return data


def _tell_coordinator_of_return(db: Session, event: Event, decision: str, reason: str) -> None:
    coordinator = event.assigned_coordinator
    if coordinator is None or not can_see_event(coordinator, event):
        return
    outcome = "was rejected" if decision == REJECT else "asked for changes"
    notify(
        db, coordinator, event, KIND_SAFETY_RETURNED,
        f'The safety check on "{event.title}" {outcome}. Reason: {reason} '
        "The event is back in Planning and its arrangements are marked for review.",
    )


@router.post("/events/{event_id}/decision")
def decide_safety_check(
    event_id: int,
    action: SafetyDecision,
    current_user: User = Depends(require_safety_officer),
    db: Session = Depends(get_db),
):
    """Record the decision and everything it causes, in one commit."""
    event = _event_in_review(event_id, db)
    new_status = CONFIRMED if action.decision == APPROVE else PLANNING
    try:
        change_status(db, event, new_status, current_user, reason=action.reason)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    db.add(SafetyCheck(event_id=event.id, decision=action.decision, reason=action.reason, decided_by_id=current_user.id))

    if action.decision == APPROVE:
        tell_organiser_and_coordinator(
            db, event, KIND_EVENT_CONFIRMED,
            f'The event "{event.title}" passed its safety check and is now Confirmed.',
        )
    else:
        arrangements.flag_event_arrangements(db, event, arrangements.SOURCE_SAFETY_REVIEW, action.reason, current_user)
        event_hooks.safety_returned(db, event, current_user, action.reason)
        _tell_coordinator_of_return(db, event, action.decision, action.reason)

    db.commit()
    db.refresh(event)
    data = event_to_dict(event)
    data["message"] = "Safety check recorded"
    return data
