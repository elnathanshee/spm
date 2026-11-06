"""[Sprint 5] Attendee registration: SCRUM-45, 46, 47, 48, 52 and part of SCRUM-65.

Open means the event's setting is on and the event is Confirmed. The setting can
be switched on earlier, but nobody can register until the event is Confirmed.

An Attendee only ever receives what attendee_event_view returns: a short whitelist,
so a new internal field on Event can never reach them by accident.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, StrictBool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import booking_rules, event_hooks
from .auth import get_current_user, normalized_role
from .database import get_db
from .models import Event, EventRegistration, EventRegistrationSetting, User
from .notifications import can_see_event, notify
from .status import CANCELLED, COMPLETED, CONFIRMED

router = APIRouter(tags=["registrations"])

REGISTERED = "registered"
WITHDRAWN = "withdrawn"

NOT_OPEN_MESSAGE = "This event isn't open for registration."
ALREADY_REGISTERED_MESSAGE = "You're already registered for this event."

# [SCRUM-52] Notification kinds. They are kept here, like the rest of the feature.
KIND_EVENT_CANCELLED = "registered_event_cancelled"
KIND_EVENT_CHANGED = "registered_event_changed"
KIND_DETAILS_CHANGED = "registered_details_changed"
KIND_VENUE_CHANGED = "registered_venue_changed"
# The routine-edit fields an Attendee can see in attendee_event_view.
ATTENDEE_VISIBLE_FIELDS = {"title", "description"}


def _require_attendee(user: User) -> None:
    if normalized_role(user) != "attendee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Attendees can use event registration.")


def _find_setting(db: Session, event_id: int):
    return db.query(EventRegistrationSetting).filter(EventRegistrationSetting.event_id == event_id).first()


def _lock_setting(db: Session, event_id: int):
    """[SCRUM-46] Hold the setting row until commit, so two people can't take the last place."""
    return (
        db.query(EventRegistrationSetting)
        .filter(EventRegistrationSetting.event_id == event_id)
        .with_for_update()
        .first()
    )


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


@router.post("/registrations/events/{event_id}", status_code=status.HTTP_201_CREATED)
def register_for_event(event_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-46: take a place. Refused when the event isn't open or is full, or I'm already registered."""
    _require_attendee(current_user)
    event = db.get(Event, event_id)
    setting = _lock_setting(db, event_id) if event else None
    # An unknown event and one that isn't open answer the same, so nothing is revealed.
    if event is None or not _is_open(event, setting):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_OPEN_MESSAGE)

    registration = _find_registration(db, event.id, current_user.id)
    if registration is not None and registration.status == REGISTERED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_REGISTERED_MESSAGE)

    capacity = event_capacity(event)
    if capacity is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The number of places for this event hasn't been set yet. Please check back later.",
        )
    if _registered_count(db, event.id) >= capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This event is full. A place may open up if someone withdraws.",
        )

    if registration is None:
        registration = EventRegistration(event_id=event.id, attendee_id=current_user.id, status=REGISTERED)
        db.add(registration)
    else:
        # Withdrawing then registering again reuses the row, so there is never a second one.
        registration.status = REGISTERED
        registration.withdrawn_at = None
        registration.created_at = booking_rules.utc_now()
    try:
        db.commit()
    except IntegrityError:
        # Another request by this Attendee got there first. The constraint is the backstop.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_REGISTERED_MESSAGE)

    data = attendee_event_view(db, event, registration)
    data["message"] = f'You are registered for "{event.title}".'
    return data


@router.get("/registrations/mine")
def my_registrations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-47: my registrations, newest first, with withdrawn ones kept as history."""
    _require_attendee(current_user)
    rows = (
        db.query(EventRegistration)
        .filter(EventRegistration.attendee_id == current_user.id)
        .order_by(EventRegistration.created_at.desc(), EventRegistration.id.desc())
        .all()
    )
    result = []
    for registration in rows:
        data = attendee_event_view(db, registration.event, registration)
        data["registered_at"] = registration.created_at
        data["withdrawn_at"] = registration.withdrawn_at
        result.append(data)
    return result


@router.post("/registrations/events/{event_id}/withdraw")
def withdraw_from_event(event_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-47: give up my place. The row stays as history. Allowed until the event is over."""
    _require_attendee(current_user)
    registration = _find_registration(db, event_id, current_user.id)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You aren't registered for this event.")
    if registration.status == WITHDRAWN:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You've already withdrawn from this event.")

    event = registration.event
    if event.status == CANCELLED or event.status == COMPLETED:
        state = _event_state(event, event.registration_setting)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This event is {state}, so there is nothing to withdraw from.",
        )

    registration.status = WITHDRAWN
    registration.withdrawn_at = booking_rules.utc_now()
    db.commit()

    data = attendee_event_view(db, event, registration)
    data["message"] = f'You have withdrawn from "{event.title}".'
    return data


def _event_for_staff(db: Session, event_id: int, user: User, allow_lead: bool = True) -> Event:
    """[SCRUM-48] The event's Organiser, its assigned Coordinator and (for reading) the Lead.

    Another Organiser gets the same 404 as GET /events/{id}, which conceals the event.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    role = normalized_role(user)
    if role == "organiser" and event.organiser_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    entitled = (
        role == "organiser"
        or (role == "coordinator" and event.assigned_coordinator_id == user.id)
        or (role == "lead" and allow_lead)
    )
    if not entitled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event's Organiser or assigned Coordinator can do this.",
        )
    return event


@router.get("/events/{event_id}/registrations")
def event_registrations(event_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-48: who registered for this event, people holding places first, then withdrawn ones as history."""
    event = _event_for_staff(db, event_id, current_user)
    rows = sorted(event.registrations, key=lambda r: (r.status != REGISTERED, r.created_at, r.id))
    return {
        "event_id": event.id,
        "capacity": event_capacity(event),
        "registered_count": _registered_count(db, event.id),
        "places_left": _places_left(db, event),
        "registrations": [
            {
                "id": r.id,
                "attendee": r.attendee.username,
                "status": r.status,
                "registered_at": r.created_at,
                "withdrawn_at": r.withdrawn_at,
            }
            for r in rows
        ],
    }


class RegistrationSettingUpdate(BaseModel):
    # StrictBool: only a real true or false switches registration, not "yes" or 1.
    enabled: StrictBool


def _setting_message(event: Event, setting) -> str:
    """[SCRUM-65] What the switch means right now, in a sentence."""
    if setting is None or not setting.enabled:
        return "Registration is off for this event."
    if event.status == CONFIRMED:
        return "Registration is open for this event."
    return "Registration is on. It opens to Attendees once the event is Confirmed."


def _setting_to_dict(event: Event, setting) -> dict:
    return {
        "event_id": event.id,
        "enabled": bool(setting and setting.enabled),
        "open": _is_open(event, setting),
        "updated_by": setting.updated_by.username if setting else None,
        "updated_at": setting.updated_at if setting else None,
        "message": _setting_message(event, setting),
    }


@router.get("/events/{event_id}/registration-settings")
def get_registration_settings(event_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SCRUM-65: whether registration is on. No row means off."""
    event = _event_for_staff(db, event_id, current_user)
    return _setting_to_dict(event, _find_setting(db, event.id))


@router.put("/events/{event_id}/registration-settings")
def update_registration_settings(
    event_id: int,
    payload: RegistrationSettingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SCRUM-65: the Organiser or assigned Coordinator switch registration on or off, at any status."""
    event = _event_for_staff(db, event_id, current_user, allow_lead=False)
    setting = _find_setting(db, event.id)
    if setting is None:
        setting = EventRegistrationSetting(event_id=event.id)
        db.add(setting)
    setting.enabled = payload.enabled
    setting.updated_by_id = current_user.id
    setting.updated_at = booking_rules.utc_now()
    try:
        db.commit()
    except IntegrityError:
        # Two first saves at the same moment. The constraint keeps a single row.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Someone else changed this setting at the same time. Please try again.",
        )
    db.refresh(setting)
    return _setting_to_dict(event, setting)


def _notify_registered(db: Session, event: Event, kind: str, message: str) -> None:
    """[SCRUM-52] One notice for each Attendee holding a place. The caller commits."""
    for registration in event.registrations:
        # A role can change after registering. notify() would raise, and that must not stop the change itself.
        if registration.status == REGISTERED and can_see_event(registration.attendee, event):
            notify(db, registration.attendee, event, kind, message)


# The hooks below pass an internal reason and internal field names. Attendee
# messages use neither, so nothing internal can reach them.
@event_hooks.on_event_cancelled
def _tell_attendees_cancelled(db, event, actor, reason):
    message = f'"{event.title}", which you registered for, has been cancelled.'
    _notify_registered(db, event, KIND_EVENT_CANCELLED, message)


@event_hooks.on_important_change
def _tell_attendees_changed(db, event, actor, changed_fields, reason):
    message = f'"{event.title}", which you registered for, has changed. Please check the date, time and venue.'
    _notify_registered(db, event, KIND_EVENT_CHANGED, message)


@event_hooks.on_details_changed
def _tell_attendees_details_changed(db, event, actor, changed_fields):
    # A routine edit of a field an Attendee can't see would be a notice about nothing.
    if not set(changed_fields) & ATTENDEE_VISIBLE_FIELDS:
        return
    message = f'The details of "{event.title}", which you registered for, have been updated.'
    _notify_registered(db, event, KIND_DETAILS_CHANGED, message)


def tell_attendees_venue_changed(db, event):
    """[SCRUM-52] A Confirmed booking sets the venue and the places an Attendee sees. The caller commits."""
    message = f'The venue for "{event.title}", which you registered for, has changed. Please check the venue and places.'
    _notify_registered(db, event, KIND_VENUE_CHANGED, message)
