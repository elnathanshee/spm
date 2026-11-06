"""SCRUM-82: A venue becomes unavailable after booking (Week 7 customer change 2).

  GET    /venues/{id}/unavailability              Coordinators, Leads, Venue Staff
  POST   /venues/{id}/unavailability              Venue Staff record a period
  DELETE /venues/{id}/unavailability/{period_id}  Venue Staff remove a period

The blocking rule itself is booking_rules.unavailability_overlaps, so the
availability check, the flag and the calendar all use one rule.

Recording a period flags every booking that holds the venue and overlaps it, and
tells each affected event's Coordinator. Nothing is cancelled, and the event's own
information doesn't change. Removing a period clears its flags, because
booking_recheck.active_conflict_flag works them out when they are shown.
"""

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session

from .booking_recheck import flag_unavailable
from .booking_rules import BLOCKING_STATUSES, is_blocking, occupied_window, period_text, to_naive_utc, utc_now, windows_overlap
from .database import get_db
from .models import User, Venue, VenueBooking, VenueUnavailability
from .venue_holds import _tell_coordinator
from .venues import booking_detail_dict, require_venue_searcher, require_venue_staff

router = APIRouter(prefix="/venues", tags=["venue unavailability"])

KIND_VENUE_UNAVAILABLE = "venue_unavailable"


class UnavailabilityCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()

    @model_validator(mode="after")
    def end_after_start(self) -> "UnavailabilityCreate":
        # [SCRUM-82] Check the years before converting: a date at the very edge of
        # the calendar overflows when it moves to UTC or to Singapore time.
        if not (2000 <= self.start_at.year <= 2999 and 2000 <= self.end_at.year <= 2999):
            raise ValueError("Dates must be between the years 2000 and 2999")
        # [SCRUM-82] Convert first: one end with an offset and one without
        # can't be compared, and that would be a crash rather than a 422.
        self.start_at = to_naive_utc(self.start_at)
        self.end_at = to_naive_utc(self.end_at)
        if self.end_at <= self.start_at:
            raise ValueError("The end must be after the start")
        return self


def period_to_dict(period: VenueUnavailability) -> Dict[str, Any]:
    return {
        "id": period.id,
        "venue_id": period.venue_id,
        "start_at": period.start_at.isoformat(),
        "end_at": period.end_at.isoformat(),
        "reason": period.reason,
        "created_at": period.created_at.isoformat() if period.created_at else None,
    }


def affected_bookings(db: Session, venue: Venue, period: VenueUnavailability) -> List[VenueBooking]:
    """The bookings a new period affects: they hold the venue, their occupied window
    overlaps the period, and they haven't ended. Nobody can make other arrangements
    for a booking that is over, so a period over the past flags nothing."""
    bookings = (
        db.query(VenueBooking)
        .filter(
            VenueBooking.venue_id == venue.id,
            VenueBooking.status.in_(BLOCKING_STATUSES),
            VenueBooking.booking_end > utc_now(),
        )
        .order_by(VenueBooking.booking_start.asc(), VenueBooking.id.asc())
        .all()
    )
    affected = []
    for booking in bookings:
        occupied_start, occupied_end = occupied_window(booking.booking_start, booking.booking_end, venue)
        if is_blocking(booking) and windows_overlap(occupied_start, occupied_end, period.start_at, period.end_at):
            affected.append(booking)
    return affected


def tell_coordinators(db: Session, venue: Venue, period: VenueUnavailability, affected: List[VenueBooking]) -> None:
    """One notice per affected event, however many of its bookings are affected.
    _tell_coordinator skips an event with no Coordinator."""
    told_events = set()
    for booking in affected:
        if booking.event_id in told_events:
            continue
        told_events.add(booking.event_id)
        _tell_coordinator(
            db, booking, KIND_VENUE_UNAVAILABLE,
            f'{venue.name} is unavailable ({period.reason}) from {period_text(period)}. '
            f'"{booking.event.title}" has a booking there in that time, and alternative arrangements are needed.',
        )


def _get_venue_or_404(venue_id: int, db: Session) -> Venue:
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    return venue


@router.get("/{venue_id}/unavailability")
def list_unavailability(
    venue_id: int,
    current_user: User = Depends(require_venue_searcher),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] The venue's periods, earliest first."""
    venue = _get_venue_or_404(venue_id, db)
    periods = (
        db.query(VenueUnavailability)
        .filter(VenueUnavailability.venue_id == venue.id)
        .order_by(VenueUnavailability.start_at.asc(), VenueUnavailability.id.asc())
        .all()
    )
    return [period_to_dict(period) for period in periods]


@router.post("/{venue_id}/unavailability", status_code=status.HTTP_201_CREATED)
def record_unavailability(
    venue_id: int,
    payload: UnavailabilityCreate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] Record a period, flag the bookings it affects, tell their
    Coordinators and return those bookings. The same period twice is refused
    (409), so a double submit can't notify anyone twice."""
    # Lock the venue row, as approvals do, so an approval at this venue can't slip
    # between the period being saved and its bookings being flagged. FOR UPDATE
    # does nothing on SQLite.
    venue = db.query(Venue).filter(Venue.id == venue_id).with_for_update().first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    duplicate = (
        db.query(VenueUnavailability)
        .filter(
            VenueUnavailability.venue_id == venue.id,
            VenueUnavailability.start_at == payload.start_at,
            VenueUnavailability.end_at == payload.end_at,
            VenueUnavailability.reason == payload.reason,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This period is already recorded for the venue")

    period = VenueUnavailability(
        venue_id=venue.id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        reason=payload.reason,
        created_by_id=current_user.id,
    )
    db.add(period)
    db.flush()
    affected = affected_bookings(db, venue, period)
    for booking in affected:
        flag_unavailable(booking, period)
    tell_coordinators(db, venue, period, affected)
    db.commit()
    db.refresh(period)
    candidates = {}  # the venue's bookings load once
    return {
        "message": "Unavailability recorded",
        "period": period_to_dict(period),
        "affected_bookings": [booking_detail_dict(booking, candidates) for booking in affected],
    }


@router.delete("/{venue_id}/unavailability/{period_id}")
def remove_unavailability(
    venue_id: int,
    period_id: int,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] Remove a period, through its own venue only."""
    period = (
        db.query(VenueUnavailability)
        .filter(VenueUnavailability.id == period_id, VenueUnavailability.venue_id == venue_id)
        .first()
    )
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unavailability period not found")
    db.delete(period)
    db.commit()
    return {"message": "Unavailability removed"}
