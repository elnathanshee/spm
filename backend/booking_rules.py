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

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import Venue, VenueBooking

# Booking statuses that hold a venue. Pending is how SCRUM-35 stores a
# tentative hold. Renaming statuses is a team decision for later.
BLOCKING_STATUSES = ["Confirmed", "Approved", "Tentative", "Pending"]


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
    """True when the booking holds its venue: its status is in BLOCKING_STATUSES.

    This is the one gate every caller uses (find_conflicts, retire_venue and
    the calendar). Callers may filter on BLOCKING_STATUSES in SQL first, but the
    final answer is always this function, so SCRUM-81 only has to change
    is_blocking to make expired holds stop blocking. No expiry yet.
    """
    return booking.status in BLOCKING_STATUSES


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
