"""[Sprint 5] Attendee registration: SCRUM-45, 46, 47, 48, 52 and part of SCRUM-65.

Open means the event's setting is on and the event is Confirmed. The setting can
be switched on earlier, but nobody can register until the event is Confirmed.

An Attendee only ever receives what attendee_event_view returns: a short whitelist,
so a new internal field on Event can never reach them by accident.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .auth import get_current_user, normalized_role
from .database import get_db
from .models import Event, EventRegistration, EventRegistrationSetting, User
from .status import CANCELLED, COMPLETED, CONFIRMED

router = APIRouter(tags=["registrations"])

REGISTERED = "registered"
WITHDRAWN = "withdrawn"


def _require_attendee(user: User) -> None:
    if normalized_role(user) != "attendee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Attendees can use event registration.")


def _find_setting(db: Session, event_id: int):
    return db.query(EventRegistrationSetting).filter(EventRegistrationSetting.event_id == event_id).first()


def _find_registration(db: Session, event_id: int, attendee_id: int):
    return (
        db.query(EventRegistration)
        .filter(EventRegistration.event_id == event_id, EventRegistration.attendee_id == attendee_id)
        .first()
    )


def _is_open(event: Event, setting) -> bool:
    return setting is not None and setting.enabled and event.status == CONFIRMED


def _event_state(event: Event, setting) -> str:
    """[SCRUM-45] What an Attendee may know about where an event stands. Internal statuses stay hidden."""
    if event.status == CANCELLED:
        return "cancelled"
    if event.status == COMPLETED:
        return "completed"
    return "open" if _is_open(event, setting) else "closed"


def _confirmed_venues(event: Event) -> list:
    """[SCRUM-45] Each venue of the event's Confirmed bookings, once. Other bookings are internal."""
    bookings = sorted(event.venue_bookings, key=lambda b: (b.booking_start, b.id))
    venues = []
    for booking in bookings:
        if booking.status == "Confirmed" and booking.venue not in venues:
            venues.append(booking.venue)
    return venues


def event_capacity(event: Event):
    """[SCRUM-46] The lower of the expected attendance and the smallest Confirmed venue.

    With several venues the smallest decides, on the safe side. Returns None when
    neither number is known.
    """
    limits = [venue.capacity for venue in _confirmed_venues(event)]
    if event.expected_attendance is not None:
        limits.append(event.expected_attendance)
    return min(limits) if limits else None


def _registered_count(db: Session, event_id: int) -> int:
    return (
        db.query(EventRegistration)
        .filter(EventRegistration.event_id == event_id, EventRegistration.status == REGISTERED)
        .count()
    )


def _places_left(db: Session, event: Event) -> int:
    capacity = event_capacity(event)
    if capacity is None:
        return 0
    # A capacity lowered below the number registered reads as no places, not a negative.
    return max(capacity - _registered_count(db, event.id), 0)


def attendee_event_view(db: Session, event: Event, registration) -> dict:
    """[SCRUM-45] The whitelist: everything an Attendee may see about an event."""
    return {
        "event_id": event.id,
        "title": event.title,
        "description": event.description,
        "proposed_start": event.proposed_start,  # naive Singapore wall time
        "venues": [{"name": v.name, "location": v.location} for v in _confirmed_venues(event)],
        "places_left": _places_left(db, event),
        "my_status": registration.status if registration else None,
        "event_state": _event_state(event, event.registration_setting),
    }


def _soonest_first(event: Event):
    return (event.proposed_start is None, event.proposed_start, event.id)


@router.get("/registrations/events")
def list_open_events(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-45: the open events, with places left and my own status."""
    _require_attendee(current_user)
    events = (
        db.query(Event)
        .join(EventRegistrationSetting, EventRegistrationSetting.event_id == Event.id)
        .filter(EventRegistrationSetting.enabled.is_(True), Event.status == CONFIRMED)
        .all()
    )
    return [
        attendee_event_view(db, event, _find_registration(db, event.id, current_user.id))
        for event in sorted(events, key=_soonest_first)
    ]
