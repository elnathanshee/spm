"""SCRUM-32: View the venue availability calendar.

GET /venues/{venue_id}/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD

For each day in the range (inclusive, at most 31 days) the calendar shows the
venue's recorded operating hours and its busy blocks. A busy block is a booking
with a blocking status, shown with its booked period and the window it
occupies the venue: start minus setup time to end plus turnaround time
(Week 7 Change 1, booking_rules.occupied_window, since SCRUM-38). A block appears
on every day its occupied window touches, so a booking over midnight appears
on both days.

AC3: the operating hours are the venue's recorded unavailability today (it is
unavailable outside them). unavailable_periods is empty, ready for the
closures Week 7 Change 2 will let Venue Staff record.
"""

from datetime import date, datetime, time, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .booking_rules import BLOCKING_STATUSES, is_blocking, occupied_window, windows_overlap  # [SCRUM-38]
from .database import get_db
from .models import User, Venue, VenueBooking
from .venues import require_venue_searcher
from .booking_rules import SINGAPORE, to_naive_utc  # [SCRUM-99]

router = APIRouter(prefix="/venues", tags=["venue calendar"])

MAX_DAYS = 31


def singapore_midnight(day: date) -> datetime:
    """[SCRUM-99] The start of a Singapore day in naive UTC, like the stored booking times."""
    return to_naive_utc(datetime.combine(day, time.min, tzinfo=SINGAPORE))


def busy_block(booking: VenueBooking, occupied_start: datetime, occupied_end: datetime) -> Dict[str, Any]:
    """No organiser or attendee details, only what the calendar needs."""
    return {
        "event_id": booking.event_id,
        "status": booking.status,
        "booked_start": booking.booking_start.isoformat(),
        "booked_end": booking.booking_end.isoformat(),
        "occupied_start": occupied_start.isoformat(),
        "occupied_end": occupied_end.isoformat(),
    }


@router.get("/{venue_id}/calendar")
def venue_calendar(
    venue_id: int,
    from_date: date = Query(..., alias="from", description="First day, YYYY-MM-DD"),
    to_date: date = Query(..., alias="to", description="Last day, inclusive, YYYY-MM-DD"),
    current_user: User = Depends(require_venue_searcher),
    db: Session = Depends(get_db),
):
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="'to' cannot be before 'from'")
    day_count = (to_date - from_date).days + 1
    if day_count > MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"The calendar covers at most {MAX_DAYS} days; this range is {day_count} days",
        )

    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_active == True).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")

    setup = timedelta(minutes=venue.setup_time_mins)
    turnaround = timedelta(minutes=venue.turnaround_time_mins)
    range_start = singapore_midnight(from_date)
    range_end = singapore_midnight(to_date + timedelta(days=1))

    # A booking is in range when its occupied window overlaps the range:
    # start - setup < range_end and end + turnaround > range_start. The buffers
    # are moved to the right-hand side so the database only compares times.
    # Windows that only touch the range do not count.
    bookings = (
        db.query(VenueBooking)
        .filter(
            VenueBooking.venue_id == venue.id,
            VenueBooking.status.in_(BLOCKING_STATUSES),
            VenueBooking.booking_start < range_end + setup,
            VenueBooking.booking_end > range_start - turnaround,
        )
        .order_by(VenueBooking.booking_start.asc())
        .all()
    )
    # [SCRUM-38] The SQL status filter is a first cut; is_blocking has the final say.
    blocks = []
    for b in [b for b in bookings if is_blocking(b)]:
        occupied_start, occupied_end = occupied_window(b.booking_start, b.booking_end, venue)
        blocks.append((occupied_start, occupied_end, busy_block(b, occupied_start, occupied_end)))

    days = []
    for i in range(day_count):
        d = from_date + timedelta(days=i)
        day_start = singapore_midnight(d)
        day_end = day_start + timedelta(days=1)
        days.append(
            {
                "date": d.isoformat(),
                "operating_hours": venue.operating_hours,
                "busy": [block for start, end, block in blocks if windows_overlap(start, end, day_start, day_end)],
            }
        )

    return {
        "venue_id": venue.id,
        "name": venue.name,
        "operating_hours": venue.operating_hours,
        "setup_time_mins": venue.setup_time_mins,
        "turnaround_time_mins": venue.turnaround_time_mins,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "unavailable_periods": [],
        "days": days,
    }
