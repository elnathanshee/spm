"""SCRUM-80 AC3 (the same criterion as SCRUM-38 AC8): re-check a venue's
existing bookings after its setup or turnaround time changes.

A booking that now clashes with another booking at the venue is flagged for
Venue Staff. It is never removed and its status never changes. The clash rule
itself is booking_rules.find_conflicts (SCRUM-38), so it stays in one place.
"""

from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from .booking_rules import BLOCKING_STATUSES, find_conflicts, is_blocking
from .models import Venue, VenueBooking, VenueBookingConflictFlag


def recheck_venue_bookings(db: Session, venue: Venue) -> List[VenueBooking]:
    """Flag every upcoming booking that holds this venue and now clashes with
    another one, using the venue's current setup and turnaround times.

    Returns only the bookings flagged by this call (new conflicts). A booking
    that already has a flag keeps it and is not reported again. The caller
    commits.
    """
    bookings = (
        db.query(VenueBooking)
        .filter(
            VenueBooking.venue_id == venue.id,
            VenueBooking.status.in_(BLOCKING_STATUSES),
            VenueBooking.booking_end > datetime.utcnow(),
        )
        .order_by(VenueBooking.booking_start.asc(), VenueBooking.id.asc())
        .all()
    )

    newly_flagged = []
    for booking in bookings:
        if not is_blocking(booking) or booking.conflict_flag is not None:
            continue
        clashes = find_conflicts(
            db, venue, booking.booking_start, booking.booking_end, exclude_booking_id=booking.id
        )
        if not clashes:
            continue
        other = clashes[0]
        booking.conflict_flag = VenueBookingConflictFlag(
            conflicting_booking_id=other.id,
            reason=(
                f"Clashes with booking #{other.id} "
                f"({other.booking_start.strftime('%Y-%m-%d %H:%M')} - {other.booking_end.strftime('%H:%M')}) "
                f"once {venue.setup_time_mins}m setup and {venue.turnaround_time_mins}m turnaround are included"
            ),
        )
        newly_flagged.append(booking)
    return newly_flagged
