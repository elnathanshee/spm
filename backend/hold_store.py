"""SCRUM-81: storing a hold's venue_holds row safely (PR 26 review items 1 and 3).

booking_rules holds the rules (when a hold expires, whether it blocks); this
module writes. It imports only models and booking_rules, so venues.py and
venue_holds.py can both use it without importing each other.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import booking_rules
from .models import VenueBooking, VenueHold


def get_or_create_hold(db: Session, booking: VenueBooking) -> VenueHold:
    """The booking's venue_holds row, created if it has none.

    The insert runs in a savepoint. If another request created the row first
    (IntegrityError on the unique booking_id), only the savepoint is rolled
    back and that row is used, so overlapping requests never fail and never
    make two rows.
    """
    if booking.hold is None:
        try:
            with db.begin_nested():
                db.add(VenueHold(booking_id=booking.id))
        except IntegrityError:
            pass  # another request created it first: use theirs
        db.expire(booking, ["hold"])
    return booking.hold


def store_default_expiry(db: Session, booking: VenueBooking) -> None:
    """Review item 3: save a new request's default expiry (booking_rules.
    default_expiry) in venue_holds, with no "set by", which marks it as the
    default. Once stored, a later change to the 48-hour rule never moves it.
    An expiry that is already stored is kept. Commits."""
    hold = get_or_create_hold(db, booking)
    if hold.expires_at is None:
        hold.expires_at = booking_rules.default_expiry(booking)
    db.commit()
