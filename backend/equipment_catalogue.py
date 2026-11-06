"""[Sprint 5] The equipment Technical Support hold, and who has reserved it.

SCRUM-41: a catalogue of equipment items. Technical Support only.
SCRUM-42: update an item, or record a number of units unavailable for a period.
SCRUM-43: availability for an event's window.
SCRUM-44: reserve available equipment for an event. A reservation has no window of
its own: it uses its event's, read live, so it follows the event's bookings.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import booking_rules, event_hooks
from .arrangements import event_window
from .auth import get_current_user
from .booking_rules import to_naive_utc, windows_overlap
from .database import get_db
from .equipment import COORDINATOR, LEAD, TECHNICAL_SUPPORT, _role_of, load_event
from .models import EquipmentItem, EquipmentItemUnavailability, EquipmentReservation, Event, User
from .status import CANCELLED, COMPLETED, REJECTED

router = APIRouter(tags=["equipment catalogue"])

# [SCRUM-41] The Integer column's limit. Above it Postgres fails with a 500, so
# the request body refuses it with a 422 first.
MAX_QUANTITY = 2147483647

# [SCRUM-43] Who can read availability and an event's reservations. A Coordinator
# also has to be the event's assigned one.
READER_ROLES = TECHNICAL_SUPPORT | {LEAD, COORDINATOR}

# [SCRUM-44] An event in one of these isn't going ahead, so a reservation would
# only keep units from the events that are.
CLOSED_EVENT_STATUSES = {CANCELLED, COMPLETED, REJECTED}


def require_technical_support(current_user: User = Depends(get_current_user)) -> User:
    """[SCRUM-41] Only Technical Support manage the catalogue."""
    if _role_of(current_user) not in TECHNICAL_SUPPORT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Technical Support Staff can manage the equipment catalogue",
        )
    return current_user


def _required_text(value: str, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} cannot be empty")
    return value.strip()


def _optional_text(value: Optional[str]) -> Optional[str]:
    # Blank is stored as "not recorded", the same as equipment.py.
    if value is None or not value.strip():
        return None
    return value.strip()


class EquipmentItemCreate(BaseModel):
    """[SCRUM-41] The lengths match the columns, so a long value gets a 422 on Postgres too."""

    equipment_type: str = Field(..., max_length=100)
    name: str = Field(..., max_length=255)
    total_quantity: int = Field(..., ge=1, le=MAX_QUANTITY)
    notes: Optional[str] = None

    @field_validator("equipment_type")
    @classmethod
    def clean_type(cls, value: str) -> str:
        return _required_text(value, "Equipment type")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value, "Name")

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)


def item_to_dict(item: EquipmentItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "equipment_type": item.equipment_type,
        "name": item.name,
        "total_quantity": item.total_quantity,
        "notes": item.notes,
    }


def _refuse_duplicate(db: Session, equipment_type: str, name: str, exclude_id: Optional[int] = None) -> None:
    """[SCRUM-41] Two rows for one item would split its units, so availability would be wrong.
    Case is ignored, as with equipment request lines (SCRUM-39)."""
    query = db.query(EquipmentItem).filter(
        func.lower(EquipmentItem.equipment_type) == equipment_type.lower(),
        func.lower(EquipmentItem.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(EquipmentItem.id != exclude_id)
    if query.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{name}' is already in the catalogue as {equipment_type}. Update that item instead.",
        )


@router.get("/equipment/items")
def list_items(
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-41] The catalogue, by type then name."""
    items = db.query(EquipmentItem).order_by(EquipmentItem.equipment_type, EquipmentItem.name).all()
    return {"items": [item_to_dict(item) for item in items]}


@router.post("/equipment/items", status_code=status.HTTP_201_CREATED)
def add_item(
    payload: EquipmentItemCreate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-41] Add one item to the catalogue."""
    _refuse_duplicate(db, payload.equipment_type, payload.name)
    item = EquipmentItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"message": "Equipment item added", "item": item_to_dict(item)}


# ---------------------------------------------------------------------------
# SCRUM-42: update an item, record units unavailable for a period
# ---------------------------------------------------------------------------


class EquipmentItemUpdate(BaseModel):
    """[SCRUM-42] Only the fields sent change. Notes can be cleared, but the
    required columns can't be set to null, or the save would be a 500."""

    equipment_type: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    total_quantity: Optional[int] = Field(None, ge=1, le=MAX_QUANTITY)
    notes: Optional[str] = None

    @field_validator("equipment_type")
    @classmethod
    def clean_type(cls, value: Optional[str]) -> str:
        return _required_text(value, "Equipment type")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> str:
        return _required_text(value, "Name")

    @field_validator("total_quantity")
    @classmethod
    def total_is_present(cls, value: Optional[int]) -> int:
        if value is None:
            raise ValueError("Total quantity cannot be empty")
        return value

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)


class UnavailabilityCreate(BaseModel):
    units: int = Field(..., ge=1, le=MAX_QUANTITY)
    start_at: datetime
    end_at: datetime
    reason: Optional[str] = None

    @field_validator("start_at", "end_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        # A time at the very edge of the calendar can't be moved to UTC.
        try:
            return to_naive_utc(value)
        except OverflowError:
            raise ValueError("That date is out of range")

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def ends_after_it_starts(self):
        if self.end_at <= self.start_at:
            raise ValueError("The period must end after it starts")
        return self


def period_to_dict(period: EquipmentItemUnavailability) -> Dict[str, Any]:
    return {
        "id": period.id,
        "item_id": period.item_id,
        "units": period.units,
        "start_at": period.start_at.isoformat(),
        "end_at": period.end_at.isoformat(),
        "reason": period.reason,
    }


def load_item(db: Session, item_id: int) -> EquipmentItem:
    item = db.query(EquipmentItem).filter(EquipmentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment item not found")
    return item


@router.patch("/equipment/items/{item_id}")
def update_item(
    item_id: int,
    payload: EquipmentItemUpdate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Update an item. A total below what's reserved is allowed, because
    units really can be lost: existing reservations stay and availability shows 0."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Send at least one field to update")
    item = load_item(db, item_id)
    _refuse_duplicate(
        db, changes.get("equipment_type", item.equipment_type), changes.get("name", item.name), exclude_id=item.id
    )
    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return {"message": "Equipment item updated", "item": item_to_dict(item)}


@router.get("/equipment/items/{item_id}/unavailability")
def list_unavailability(
    item_id: int,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] The item's unavailable periods, soonest first."""
    item = load_item(db, item_id)
    periods = (
        db.query(EquipmentItemUnavailability)
        .filter(EquipmentItemUnavailability.item_id == item.id)
        .order_by(EquipmentItemUnavailability.start_at, EquipmentItemUnavailability.id)
        .all()
    )
    return {"item_id": item.id, "periods": [period_to_dict(period) for period in periods]}


@router.post("/equipment/items/{item_id}/unavailability", status_code=status.HTTP_201_CREATED)
def record_unavailability(
    item_id: int,
    payload: UnavailabilityCreate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Record a number of units as unavailable for a period."""
    item = load_item(db, item_id)
    if payload.units > item.total_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This item only has {item.total_quantity} units in total, so {payload.units} can't be unavailable.",
        )
    period = EquipmentItemUnavailability(item_id=item.id, created_by_id=current_user.id, **payload.model_dump())
    db.add(period)
    db.commit()
    db.refresh(period)
    return {"message": "Unavailable units recorded", "period": period_to_dict(period)}


@router.delete("/equipment/items/{item_id}/unavailability/{period_id}")
def remove_unavailability(
    item_id: int,
    period_id: int,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Remove a period, for example when units come back early."""
    period = (
        db.query(EquipmentItemUnavailability)
        .filter(EquipmentItemUnavailability.id == period_id, EquipmentItemUnavailability.item_id == item_id)
        .first()
    )
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unavailable period not found")
    db.delete(period)
    db.commit()
    return {"message": "Unavailable period removed"}


# ---------------------------------------------------------------------------
# SCRUM-43: availability for an event's date and time
# ---------------------------------------------------------------------------


def reserved_units(db: Session, item: EquipmentItem, start: datetime, end: datetime) -> int:
    """[SCRUM-43] Units in active reservations whose event's window overlaps. An event
    that has lost its window (no date, no booking) can't overlap anything."""
    reservations = (
        db.query(EquipmentReservation)
        .filter(EquipmentReservation.item_id == item.id, EquipmentReservation.released_at.is_(None))
        .all()
    )
    total = 0
    for reservation in reservations:
        window = event_window(reservation.event)
        if window and windows_overlap(start, end, window[0], window[1]):
            total += reservation.quantity
    return total


def unavailable_units(db: Session, item: EquipmentItem, start: datetime, end: datetime) -> int:
    """[SCRUM-43] Units in unavailable periods that overlap."""
    periods = db.query(EquipmentItemUnavailability).filter(EquipmentItemUnavailability.item_id == item.id).all()
    return sum(period.units for period in periods if windows_overlap(start, end, period.start_at, period.end_at))


def available_units(db: Session, item: EquipmentItem, start: datetime, end: datetime) -> int:
    """[SCRUM-43] The PO's formula: total - reservations - unavailable, never below 0.
    Everything overlapping the window counts, even if those times don't overlap each
    other. That's deliberately simple, and on the safe side."""
    return max(item.total_quantity - reserved_units(db, item, start, end) - unavailable_units(db, item, start, end), 0)


def load_readable_event(db: Session, event_id: int, user: User) -> Event:
    """[SCRUM-43] Technical Support, the Lead and the event's assigned Coordinator.
    The role comes first, so nobody else can learn which events exist."""
    role = _role_of(user)
    if role not in READER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Technical Support Staff, the Lead and the event's Coordinator can view this",
        )
    event = load_event(db, event_id)
    if role == COORDINATOR and event.assigned_coordinator_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coordinators can view this only for events assigned to them",
        )
    return event


def require_window(event: Event):
    """[SCRUM-43] The event's window, or a 400 that says what to do about it."""
    window = event_window(event)
    if window is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This event has no date or venue booking yet, so its equipment can't be checked or reserved.",
        )
    return window


@router.get("/equipment/availability")
def equipment_availability(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """[SCRUM-43] How many units of each item are free for this event's window."""
    event = load_readable_event(db, event_id, current_user)
    start, end = require_window(event)
    rows = []
    for item in db.query(EquipmentItem).order_by(EquipmentItem.equipment_type, EquipmentItem.name).all():
        rows.append(
            {
                "item_id": item.id,
                "equipment_type": item.equipment_type,
                "name": item.name,
                "total_quantity": item.total_quantity,
                "reserved": reserved_units(db, item, start, end),
                "unavailable": unavailable_units(db, item, start, end),
                "available": available_units(db, item, start, end),
            }
        )
    return {
        "event_id": event.id,
        "event_title": event.title,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "items": rows,
    }


# ---------------------------------------------------------------------------
# SCRUM-44: reserve available equipment for an event
# ---------------------------------------------------------------------------


class ReservationCreate(BaseModel):
    item_id: int = Field(..., ge=1, le=MAX_QUANTITY)
    quantity: int = Field(..., ge=1, le=MAX_QUANTITY)


def reservation_to_dict(reservation: EquipmentReservation) -> Dict[str, Any]:
    item = reservation.item
    return {
        "id": reservation.id,
        "event_id": reservation.event_id,
        "item_id": reservation.item_id,
        "item_name": item.name,
        "equipment_type": item.equipment_type,
        "quantity": reservation.quantity,
        "created_by": reservation.created_by.username,
        "created_at": reservation.created_at.isoformat(),
        "released_at": reservation.released_at.isoformat() if reservation.released_at else None,
        "released_by": reservation.released_by.username if reservation.released_by else None,
    }


def release_reservation(reservation: EquipmentReservation, actor: User) -> None:
    """[SCRUM-44] Keep the row: it's the record of what was held. The caller commits."""
    reservation.released_at = booking_rules.utc_now()
    reservation.released_by_id = actor.id


@event_hooks.on_event_cancelled
def release_reservations_of_cancelled_event(db: Session, event: Event, actor: User, reason: str) -> None:
    """[SCRUM-44] A cancelled event holds nothing. Runs in the caller's transaction, so no commit."""
    active = (
        db.query(EquipmentReservation)
        .filter(EquipmentReservation.event_id == event.id, EquipmentReservation.released_at.is_(None))
        .all()
    )
    for reservation in active:
        release_reservation(reservation, actor)


@router.get("/events/{event_id}/equipment-reservations")
def list_reservations(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """[SCRUM-44] Everything reserved for this event, including what has been released."""
    event = load_readable_event(db, event_id, current_user)
    reservations = (
        db.query(EquipmentReservation)
        .filter(EquipmentReservation.event_id == event.id)
        .order_by(EquipmentReservation.id.asc())
        .all()
    )
    return {"event_id": event.id, "reservations": [reservation_to_dict(row) for row in reservations]}


@router.post("/events/{event_id}/equipment-reservations", status_code=status.HTTP_201_CREATED)
def reserve_equipment(
    event_id: int,
    payload: ReservationCreate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-44] Reserve units for an event. Refused if not enough are available."""
    event = load_event(db, event_id)
    if event.status in CLOSED_EVENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This event is {event.status.lower()}, so equipment can't be reserved for it.",
        )
    start, end = require_window(event)

    # Hold the item's row until the commit, so two reservations can't both count
    # the same last unit. SQLite ignores this; Postgres enforces it.
    item = db.query(EquipmentItem).filter(EquipmentItem.id == payload.item_id).with_for_update().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment item not found")

    available = available_units(db, item, start, end)
    if payload.quantity > available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only {available} of '{item.name}' available for this event's date and time, "
                f"so {payload.quantity} can't be reserved."
            ),
        )

    reservation = EquipmentReservation(
        item_id=item.id, event_id=event.id, quantity=payload.quantity, created_by_id=current_user.id
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return {"message": "Equipment reserved", "reservation": reservation_to_dict(reservation)}


@router.delete("/events/{event_id}/equipment-reservations/{reservation_id}")
def release_equipment(
    event_id: int,
    reservation_id: int,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-44] Release a reservation, so its units are available again."""
    reservation = (
        db.query(EquipmentReservation)
        .filter(EquipmentReservation.id == reservation_id, EquipmentReservation.event_id == event_id)
        .first()
    )
    if not reservation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment reservation not found")
    # Refused rather than repeated, so the first release's name and time stay on record.
    if reservation.released_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This reservation has already been released.",
        )
    release_reservation(reservation, current_user)
    db.commit()
    db.refresh(reservation)
    return {"message": "Equipment released", "reservation": reservation_to_dict(reservation)}
