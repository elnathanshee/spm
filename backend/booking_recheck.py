"""SCRUM-80 AC3 (the same criterion as SCRUM-38 AC8): re-check a venue's
existing bookings after its setup or turnaround time changes.

A booking that now clashes with another booking at the venue is flagged for
Venue Staff. It is never removed and its status never changes. The clash rule
itself is booking_rules.find_conflicts (SCRUM-38), so it stays in one place.

GitHub #29: a stored flag can go stale once the booking it names stops holding
the venue (its hold expires, or it is rejected). active_conflict_flag
revalidates a flag each time it is shown; the stored row is kept as history.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .booking_rules import BLOCKING_STATUSES, conflicts_among, find_conflicts, is_blocking, venue_blocking_candidates
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
            reason=clash_reason(other, venue),
        )
        newly_flagged.append(booking)
    return newly_flagged


def clash_reason(other: VenueBooking, venue: Venue) -> str:
    """The message a flag shows for a clash with booking `other`."""
    return (
        f"Clashes with booking #{other.id} "
        f"({other.booking_start.strftime('%Y-%m-%d %H:%M')} - {other.booking_end.strftime('%H:%M')}) "
        f"once {venue.setup_time_mins}m setup and {venue.turnaround_time_mins}m turnaround are included"
    )


def active_conflict_flag(
    db: Session, booking: VenueBooking, candidates_by_venue: Optional[Dict[int, List[VenueBooking]]] = None
) -> Optional[Dict[str, Any]]:
    """GitHub #29: the booking's flag as it stands now, or None if no clash remains.

    A stored flag is only a record of a clash found earlier. It is shown only
    while the flagged booking still holds the venue and still clashes with a
    booking that holds it (E1, E2). If the booking it names has stopped holding
    the venue but another real clash exists, the flag names that clash (E3).
    The stored row is never changed or removed, so the history is kept (E5).
    Only flagged bookings are re-checked, so unflagged ones cost nothing.

    PR #35 review: the reason shown is rebuilt from the venue's current setup
    and turnaround (the stored reason stays as history). Pass the same
    candidates_by_venue dict for every booking in a list, so each venue's
    bookings are loaded once, not once per flagged booking.
    """
    flag = booking.conflict_flag
    if flag is None or not is_blocking(booking):
        return None
    venue = booking.venue
    if candidates_by_venue is None:
        candidates_by_venue = {}
    if venue.id not in candidates_by_venue:
        candidates_by_venue[venue.id] = venue_blocking_candidates(db, venue)
    clashes = conflicts_among(candidates_by_venue[venue.id], venue, booking.booking_start,
                              booking.booking_end, exclude_booking_id=booking.id)
    if not clashes:
        return None
    current = next((c for c in clashes if c.id == flag.conflicting_booking_id), clashes[0])
    return {
        "conflicting_booking_id": current.id,
        "reason": clash_reason(current, venue),
        "flagged_at": flag.flagged_at.isoformat() if flag.flagged_at else None,
    }


def conflict_flag_record(booking: VenueBooking, active: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """GitHub #29 (E5): the stored flag as first raised, for auditing, marked
    "Active" while a clash remains (active_conflict_flag's result) and
    "Resolved" once none does. Like SCRUM-81's "Expired", the state is worked
    out when shown, so the stored row is never changed."""
    flag = booking.conflict_flag
    if flag is None:
        return None
    return {
        "conflicting_booking_id": flag.conflicting_booking_id,
        "reason": flag.reason,
        "flagged_at": flag.flagged_at.isoformat() if flag.flagged_at else None,
        "state": "Active" if active is not None else "Resolved",
    }
