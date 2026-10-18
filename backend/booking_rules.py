"""SCRUM-38 (part 1): one rule for which bookings block a venue.

Before this module the rule lived in three places: check_venue_availability
in venues.py (its own status list and window maths), BLOCKING_BOOKING_STATUSES
for retire_venue in venues.py, and a copy plus its own window maths in
venue_calendar.py. Every caller now uses the functions below, so a later
change to the rule (hold expiry in SCRUM-81, approval in SCRUM-36) is made
in one place.

Week 7 change 1 (AC5, AC6): a booking occupies its venue from its start minus
the venue's setup time to its end plus the venue's turnaround time, and two
bookings clash when those occupied windows overlap. Windows that only touch
do not clash.

Plain functions only: no router, no endpoint.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import Venue, VenueBooking

# Booking statuses that hold a venue. Pending is how SCRUM-35 stores a
# tentative hold. Renaming statuses is a team decision for later.
BLOCKING_STATUSES = ["Confirmed", "Approved", "Tentative", "Pending"]

# [SCRUM-81] Tentative holds expire. A hold is a Pending booking (D1); once its
# expiry has passed it is stored and shown as Expired.
HOLD_STATUS = "Pending"
EXPIRED_STATUS = "Expired"
DEFAULT_HOLD_HOURS = 48  # team assumption (option O-31)
DEFAULT_WARNING_MINUTES = 24 * 60  # team assumption (option O-33)


def utc_now() -> datetime:
    """SCRUM-81 D7: the one clock: UTC without a time zone, like the stored dates.

    Always call it as booking_rules.utc_now(), so tests can set the clock in
    one place.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(moment: datetime) -> datetime:
    """SCRUM-81 D7: a time sent with a time zone is converted to UTC, then the
    zone is dropped. A time without one is taken as UTC already."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _is_hold(booking: VenueBooking) -> bool:
    return booking.status in (HOLD_STATUS, EXPIRED_STATUS)


def hold_expires_at(booking: VenueBooking) -> Optional[datetime]:
    """SCRUM-81 D2: when the hold expires: the time Venue Staff set, if any;
    otherwise 48 hours after the request, or the booking's start if that is
    sooner. None for a booking that isn't a hold (Pending or Expired), or that
    hasn't been saved yet (no created_at)."""
    if not _is_hold(booking):
        return None
    if booking.hold is not None and booking.hold.expires_at is not None:
        return booking.hold.expires_at
    if booking.created_at is None:
        return None
    return min(booking.created_at + timedelta(hours=DEFAULT_HOLD_HOURS), booking.booking_start)


def warning_minutes(booking: VenueBooking) -> Optional[int]:
    """SCRUM-81 D4: how long before expiry the Coordinator is warned (default
    24 hours). None for a booking that isn't a hold."""
    if not _is_hold(booking):
        return None
    if booking.hold is not None and booking.hold.warning_minutes is not None:
        return booking.hold.warning_minutes
    return DEFAULT_WARNING_MINUTES


def is_expired_hold(booking: VenueBooking) -> bool:
    """SCRUM-81 D5: True for a hold stored as Expired, or a Pending hold whose
    expiry has come (at exactly the expiry it has expired). Confirmed bookings
    never expire."""
    if booking.status == EXPIRED_STATUS:
        return True
    expires = hold_expires_at(booking)
    return expires is not None and utc_now() >= expires


def occupied_window(start: datetime, end: datetime, venue: Venue) -> Tuple[datetime, datetime]:
    """AC5: the window a booking from start to end occupies the venue: start
    minus the venue's setup time to end plus its turnaround time. Both columns
    are required (defaults 30 and 45 minutes).

    Example: 10:00-12:00 with 30 min setup and 45 min turnaround -> 09:30-12:45.
    """
    return (
        start - timedelta(minutes=venue.setup_time_mins),
        end + timedelta(minutes=venue.turnaround_time_mins),
    )


def windows_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True only when the two windows overlap. Windows that only touch (one
    ends exactly when the other starts) do not clash."""
    return a_start < b_end and b_start < a_end


def is_blocking(booking: VenueBooking) -> bool:
    """True when the booking holds its venue: its status is in BLOCKING_STATUSES
    and it isn't an expired hold (SCRUM-81 AC2, SCRUM-38 AC7).

    This is the one gate every caller uses (find_conflicts, retire_venue and
    the calendar). Callers may filter on BLOCKING_STATUSES in SQL first, but the
    final answer is always this function.
    """
    return booking.status in BLOCKING_STATUSES and not is_expired_hold(booking)


def hold_fields(booking: VenueBooking) -> Dict[str, Any]:
    """SCRUM-81 AC1, AC3: what every booking view shows about the hold: status
    ("Expired" once the hold has expired), hold_expires_at and warning_minutes."""
    expires = hold_expires_at(booking)
    return {
        "status": EXPIRED_STATUS if is_expired_hold(booking) else booking.status,
        "hold_expires_at": expires.isoformat() if expires else None,
        "warning_minutes": warning_minutes(booking),
    }


def expired_hold_refusal(booking: VenueBooking) -> Optional[str]:
    """SCRUM-81 D5: the 409 message for approving, rejecting or changing a hold
    that has expired, or None if it hasn't."""
    if not is_expired_hold(booking):
        return None
    return (
        f"This hold expired at {hold_expires_at(booking).strftime('%Y-%m-%d %H:%M')} UTC. "
        "The Coordinator must submit a new request."
    )


def find_conflicts(
    db: Session,
    venue: Venue,
    start: datetime,
    end: datetime,
    exclude_booking_id: Optional[int] = None,
) -> List[VenueBooking]:
    """AC1, AC6: the blocking bookings at this venue whose occupied window
    overlaps the window a booking from start to end would occupy, in start
    order (then id).

    exclude_booking_id leaves one booking out, so an existing booking can be
    checked against the venue's other bookings without clashing with itself.
    """
    requested_start, requested_end = occupied_window(start, end, venue)

    query = db.query(VenueBooking).filter(
        VenueBooking.venue_id == venue.id,
        VenueBooking.status.in_(BLOCKING_STATUSES),
    )
    if exclude_booking_id is not None:
        query = query.filter(VenueBooking.id != exclude_booking_id)
    candidates = query.order_by(VenueBooking.booking_start.asc(), VenueBooking.id.asc()).all()

    conflicts = []
    for booking in candidates:
        if not is_blocking(booking):
            continue
        booked_start, booked_end = occupied_window(booking.booking_start, booking.booking_end, venue)
        if windows_overlap(requested_start, requested_end, booked_start, booked_end):
            conflicts.append(booking)
    return conflicts
