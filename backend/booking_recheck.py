"""SCRUM-80 AC3 (the same criterion as SCRUM-38 AC8): re-check a venue's
existing bookings after its setup or turnaround time changes.

A booking that now clashes with another booking at the venue is flagged for
Venue Staff. It is never removed and its status never changes. The clash rule
itself is booking_rules.find_conflicts (SCRUM-38), so it stays in one place.

GitHub #29: a stored flag can go stale once the booking it names stops holding
the venue (its hold expires, or it is rejected). active_conflict_flag
revalidates a flag each time it is shown; the stored row is kept as history.

[SCRUM-82] A period when the venue is unavailable uses the same flag. The table
needs a conflicting booking and allows one row per booking, so such a flag names
the booking itself, and a booking already flagged keeps its row. What is shown is
worked out live, so removing the period clears the flag with no further work.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .booking_rules import BLOCKING_STATUSES, conflicts_among, find_conflicts, is_blocking, singapore_time, venue_blocking_candidates
from .booking_rules import unavailability_overlaps, unavailable_reason, utc_now  # [SCRUM-82]
from .models import Venue, VenueBooking, VenueBookingConflictFlag


def recheck_venue_bookings(db: Session, venue: Venue) -> List[VenueBooking]:
    """Flag every upcoming booking that holds this venue and now clashes with
    another one, using the venue's current setup and turnaround times.

    Returns only the bookings flagged by this call (new conflicts). A booking
    that already has a flag keeps it and is not reported again. The caller
    commits.

    [SCRUM-82] Longer buffers can also reach an unavailability period, so that
    is checked first. A row naming the booking itself came from a period, not a
    clash, so a real clash found later still counts as new and takes over the row.
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
        if not is_blocking(booking):
            continue
        flag = booking.conflict_flag
        if flag is not None and flag.conflicting_booking_id != booking.id:
            continue
        periods = unavailability_overlaps(venue, booking.booking_start, booking.booking_end)
        if flag is None and periods:
            flag_unavailable(booking, periods[0])
            newly_flagged.append(booking)
            continue
        clashes = find_conflicts(
            db, venue, booking.booking_start, booking.booking_end, exclude_booking_id=booking.id
        )
        if not clashes:
            continue
        other = clashes[0]
        if flag is None:
            booking.conflict_flag = VenueBookingConflictFlag(
                conflicting_booking_id=other.id,
                reason=clash_reason(other, venue),
            )
        else:
            flag.conflicting_booking_id = other.id
            flag.reason = clash_reason(other, venue)
            flag.flagged_at = utc_now()
        newly_flagged.append(booking)
    return newly_flagged


def flag_unavailable(booking: VenueBooking, period: Any) -> None:
    """[SCRUM-82] Flag a booking for an unavailability period, naming itself. A
    row from an earlier period is reused and updated. A row naming another booking
    is left as the record of that clash. Status and event don't change."""
    flag = booking.conflict_flag
    if flag is None:
        booking.conflict_flag = VenueBookingConflictFlag(
            conflicting_booking_id=booking.id,
            reason=unavailable_reason(period),
        )
    elif flag.conflicting_booking_id == booking.id:
        flag.reason = unavailable_reason(period)
        flag.flagged_at = utc_now()


def clash_reason(other: VenueBooking, venue: Venue) -> str:
    """The message a flag shows for a clash with booking `other`."""
    return (
        f"Clashes with booking #{other.id} "
        f"({singapore_time(other.booking_start).strftime('%Y-%m-%d %H:%M')} - {singapore_time(other.booking_end).strftime('%H:%M')}) "
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
    A row recording a real clash is never changed or removed, so the history is
    kept (E5). [SCRUM-82] A row from a period names the booking itself, and the
    next period or real clash updates it.
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
    # [SCRUM-82] Checked first, because the venue can't be used at all. A venue's
    # periods load once per session, however many of its bookings are flagged.
    periods = unavailability_overlaps(venue, booking.booking_start, booking.booking_end)
    if periods:
        return {
            "conflicting_booking_id": booking.id,
            "unavailability_id": periods[0].id,
            "reason": unavailable_reason(periods[0]),
            "flagged_at": flag.flagged_at.isoformat() if flag.flagged_at else None,
        }
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
    """GitHub #29 (E5): the stored flag, for auditing, marked "Active" while a
    clash remains (active_conflict_flag's result) and "Resolved" once none does.
    Like SCRUM-81's "Expired", the state is worked out when shown. A real clash's
    row stays as first raised. [SCRUM-82] A period's row shows the latest period
    or clash, because the next one updates it."""
    flag = booking.conflict_flag
    if flag is None:
        return None
    return {
        "conflicting_booking_id": flag.conflicting_booking_id,
        "reason": flag.reason,
        "flagged_at": flag.flagged_at.isoformat() if flag.flagged_at else None,
        "state": "Active" if active is not None else "Resolved",
    }
