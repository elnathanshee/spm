"""SCRUM-81: Tentative venue holds expire.

A hold is a Pending booking request (SCRUM-35). Whether it has expired is
worked out from the clock in booking_rules (hold_expires_at, is_expired_hold),
so nothing runs on a timer: the app is on serverless hosting.

This module adds:
  PUT /venues/bookings/{booking_id}/hold          Venue Staff set or extend the expiry (D3)
  PUT /venues/bookings/{booking_id}/hold/warning  the event's Coordinator sets the warning time (D4)
  run_hold_checks                                 sends the warning and expiry notices, once
                                                  each, and stores "Expired" (D4, D5)

run_hold_checks is attached in main.py as a router-level dependency of the
events, notifications, venues and venue_holds routers, so it runs at the start
of each of their requests. Team assumption: in-app notices appear on the next
request, not at the exact minute. Notices go to the event's assigned
Coordinator only (D8).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import booking_rules, hold_store
from .auth import get_current_user
from .database import get_db
from .models import User, VenueBooking, VenueHold
from .notifications import can_see_event, notify
from .venues import _get_booking_or_404, booking_detail_dict, require_venue_staff

router = APIRouter(prefix="/venues", tags=["venue holds"])

KIND_HOLD_EXPIRY_SET = "hold_expiry_set"
KIND_HOLD_WARNING = "hold_warning"
KIND_HOLD_EXPIRED = "hold_expired"


class HoldExpiryUpdate(BaseModel):
    expires_at: datetime


class HoldWarningUpdate(BaseModel):
    warning_minutes: int = Field(ge=0)


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _describe(booking: VenueBooking) -> str:
    """The venue, the event and the period, as the notices name them."""
    period = f"{_fmt(booking.booking_start)} - {booking.booking_end.strftime('%H:%M')}"
    return f'{booking.venue.name} for "{booking.event.title}" ({period})'


def _tell_coordinator(db: Session, booking: VenueBooking, kind: str, message: str) -> None:
    """D8: notify the event's assigned Coordinator. Nobody is told if the event
    has no Coordinator, or if they may no longer see it (so notify never raises
    NotEntitled here)."""
    event = booking.event
    coordinator = event.assigned_coordinator
    if coordinator is None or not can_see_event(coordinator, event):
        return
    notify(db, coordinator, event, kind, message)


def _refuse_unless_live_hold(booking: VenueBooking) -> None:
    """D3, D4: only a Pending hold that hasn't expired can be changed (409)."""
    refusal = booking_rules.expired_hold_refusal(booking)
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
    if booking.status != booking_rules.HOLD_STATUS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only a Pending hold has an expiry (status: {booking.status})",
        )


def check_hold(db: Session, booking: VenueBooking, now: datetime) -> bool:
    """D4, D5 for one Pending hold. Returns whether anything changed.

    At expiry, the hold is claimed with an UPDATE that sets its status to
    Expired only WHERE it is still Pending; the expiry notice is sent only if
    that changed a row. At the warning time, the warning is claimed with an
    UPDATE that sets warning_sent_at only WHERE it is still null; the warning is
    sent only if that changed a row. So each notice is sent exactly once, even
    when two checks overlap (PR 26 review item 1): the database, not what this
    session loaded earlier, decides who sends it. If the hold has already
    expired, only the expiry notice is sent.
    """
    expires = booking_rules.hold_expires_at(booking)
    if now >= expires:
        claimed = (
            db.query(VenueBooking)
            .filter(VenueBooking.id == booking.id, VenueBooking.status == booking_rules.HOLD_STATUS)
            .update({VenueBooking.status: booking_rules.EXPIRED_STATUS}, synchronize_session=False)
        )
        if not claimed:
            return False
        db.expire(booking, ["status"])
        _tell_coordinator(
            db, booking, KIND_HOLD_EXPIRED,
            f"The hold on {_describe(booking)} expired at {_fmt(expires)} UTC. The venue is free "
            "for other requests; submit a new request if you still need it.",
        )
        hold_store.get_or_create_hold(db, booking).expiry_notice_sent_at = now
        return True

    if now < expires - timedelta(minutes=booking_rules.warning_minutes(booking)):
        return False
    hold = hold_store.get_or_create_hold(db, booking)
    claimed = (
        db.query(VenueHold)
        .filter(VenueHold.id == hold.id, VenueHold.warning_sent_at.is_(None))
        .update({VenueHold.warning_sent_at: now}, synchronize_session=False)
    )
    if not claimed:
        return False
    db.expire(hold, ["warning_sent_at"])
    _tell_coordinator(
        db, booking, KIND_HOLD_WARNING,
        f"The hold on {_describe(booking)} expires at {_fmt(expires)} UTC. "
        "Ask Venue Staff to confirm or extend it.",
    )
    return True


def run_hold_checks(db: Session = Depends(get_db)) -> None:
    """D4, D5: check every Pending hold (check_hold): send the Coordinator the
    warning once it is due, and at expiry the expiry notice, storing the status
    Expired. One query; commits only when something changed, so each claim and
    its notice are saved together."""
    now = booking_rules.utc_now()
    changed = False
    for booking in db.query(VenueBooking).filter(VenueBooking.status == booking_rules.HOLD_STATUS).all():
        changed = check_hold(db, booking, now) or changed
    if changed:
        db.commit()


@router.put("/bookings/{booking_id}/hold")
def set_hold_expiry(
    booking_id: int,
    payload: HoldExpiryUpdate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """D3: Venue Staff set or extend a hold's expiry. It must be in the future
    and no later than the booking's start (422). The Coordinator is told, and
    the warning is due again for the new time."""
    booking = _get_booking_or_404(booking_id, db)
    _refuse_unless_live_hold(booking)

    expires_at = booking_rules.to_naive_utc(payload.expires_at)
    now = booking_rules.utc_now()
    if expires_at <= now:
        raise HTTPException(status_code=422, detail="The new expiry must be in the future")
    if expires_at > booking.booking_start:
        raise HTTPException(
            status_code=422,
            detail=f"The new expiry can't be later than the booking's start ({_fmt(booking.booking_start)})",
        )

    hold = hold_store.get_or_create_hold(db, booking)
    hold.expires_at = expires_at
    hold.expires_set_by_id = current_user.id
    hold.expires_set_at = now
    hold.warning_sent_at = None
    _tell_coordinator(
        db, booking, KIND_HOLD_EXPIRY_SET,
        f"The hold on {_describe(booking)} now expires at {_fmt(expires_at)} UTC.",
    )
    db.commit()
    db.refresh(booking)
    return {"message": "Hold expiry updated", "booking": booking_detail_dict(booking)}


@router.put("/bookings/{booking_id}/hold/warning")
def set_hold_warning(
    booking_id: int,
    payload: HoldWarningUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """D4: the event's assigned Coordinator sets how many minutes before expiry
    they are warned. A changed warning time is due again (PO answer 3)."""
    refused = "Only the event's assigned Coordinator can set the warning time"
    # Role check before the lookup, so other roles learn nothing about the booking.
    if (current_user.role or "").strip().lower() != "coordinator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refused)
    booking = _get_booking_or_404(booking_id, db)
    if booking.event.assigned_coordinator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refused)
    _refuse_unless_live_hold(booking)

    hold = hold_store.get_or_create_hold(db, booking)
    hold.warning_minutes = payload.warning_minutes
    hold.warning_sent_at = None
    db.commit()
    db.refresh(booking)
    return {"message": "Warning time updated", "booking": booking_detail_dict(booking)}
