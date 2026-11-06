"""SCRUM-34: See whether a venue appears suitable for an event.

GET /events/{event_id}/venue-suitability?facilities=Projector,Stage

For every active venue, two checks are run against the event's requirements:
  capacity    the venue's capacity against the event's expected attendance
  facilities  the venue's recorded facilities against the facilities the
              Coordinator states in ``facilities`` (events only store
              requirements as free text, so they can't be read from the event)

Each check is pass, fail or unknown, with a plain-English detail. The venue's
overall suitability: any fail -> unsuitable; else any unknown -> unknown;
else suitable. Missing information is never treated as a pass.

Advisory only: nothing is booked or changed. Venue Staff keep the final say.
"""

from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .arrangements import event_window  # [SCRUM-82]
from .auth import get_current_user
from .booking_rules import period_text, windows_overlap  # [SCRUM-82]
from .database import get_db
from .models import Event, User, Venue
from .venues import deserialize_list

router = APIRouter(prefix="/events", tags=["suitability"])

LEAD = "lead"
COORDINATOR = "coordinator"
ALLOWED_ROLES = {LEAD, COORDINATOR}

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

SUITABLE = "suitable"
UNSUITABLE = "unsuitable"


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


def _check(name: str, result: str, detail: str) -> dict:
    return {"check": name, "result": result, "detail": detail}


def parse_required_facilities(raw: Optional[str]) -> Optional[List[str]]:
    """None when the parameter was left out ("not stated").

    Otherwise split on commas, trim, drop blanks, and drop case-insensitive
    duplicates keeping the first spelling. So "" and " , " both give [] (none
    required), and "Stage,stage" gives ["Stage"].
    """
    if raw is None:
        return None
    required = []
    seen = set()
    for item in raw.split(","):
        name = item.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        required.append(name)
    return required


def recorded_facilities(venue: Venue) -> Optional[List[str]]:
    """None when the venue's facilities were never recorded.

    NULL, "" and blank text all count as never recorded. This has to be checked
    before deserialize_list, which returns [] for all three and for a recorded
    empty list ("[]") alike.
    """
    if venue.facilities is None or not venue.facilities.strip():
        return None
    return deserialize_list(venue.facilities)


def capacity_check(capacity: int, expected_attendance: Optional[int]) -> dict:
    if expected_attendance is None:
        return _check("capacity", UNKNOWN, "event has no expected attendance recorded")
    if capacity >= expected_attendance:
        return _check("capacity", PASS, f"capacity {capacity} meets expected attendance {expected_attendance}")
    return _check("capacity", FAIL, f"capacity {capacity} is below expected attendance {expected_attendance}")


def facilities_check(venue_facilities: Optional[List[str]], required: Optional[List[str]]) -> dict:
    if required is None:
        return _check("facilities", UNKNOWN, "required facilities not stated")
    if not required:
        return _check("facilities", PASS, "no facilities required")
    if venue_facilities is None:
        return _check("facilities", UNKNOWN, "venue facilities not recorded")
    has = {f.strip().lower() for f in venue_facilities}
    missing = [f for f in required if f.lower() not in has]
    if missing:
        return _check("facilities", FAIL, "missing: " + ", ".join(missing))
    return _check("facilities", PASS, "has all required facilities: " + ", ".join(required))


def availability_check(venue: Venue, window: Optional[Tuple[datetime, datetime]]) -> Optional[dict]:
    """[SCRUM-82] A failed check when an unavailability period overlaps the
    event's window, naming the reason. None otherwise: a venue with no such
    period keeps its usual two checks."""
    if window is None:
        return None
    overlaps = [p for p in venue.unavailable_periods if windows_overlap(window[0], window[1], p.start_at, p.end_at)]
    if not overlaps:
        return None
    period = min(overlaps, key=lambda p: p.start_at)
    return _check("availability", FAIL, f"unavailable ({period.reason}) from {period_text(period)}")


def overall_suitability(checks: List[dict]) -> str:
    results = [c["result"] for c in checks]
    if FAIL in results:
        return UNSUITABLE
    if UNKNOWN in results:
        return UNKNOWN
    return SUITABLE


@router.get("/{event_id}/venue-suitability")
def venue_suitability(
    event_id: int,
    facilities: Optional[str] = Query(
        None,
        description="Comma-separated required facilities. Leave out if not stated; send empty if none are required.",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role = _role_of(current_user)

    # Role check before the lookup, so other roles learn nothing about the event.
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned Coordinator or a Lead can check venue suitability",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Week 7 Change #5: coordinators manage only the events assigned to them.
    if role == COORDINATOR and event.assigned_coordinator_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only check venue suitability for events assigned to you",
        )

    required = parse_required_facilities(facilities)
    venues = db.query(Venue).filter(Venue.is_active == True).order_by(Venue.name.asc()).all()
    window = event_window(event)  # [SCRUM-82]

    results = []
    for venue in venues:
        venue_facilities = recorded_facilities(venue)
        checks = [
            capacity_check(venue.capacity, event.expected_attendance),
            facilities_check(venue_facilities, required),
        ]
        unavailable = availability_check(venue, window)  # [SCRUM-82]
        if unavailable:
            checks.append(unavailable)
        results.append(
            {
                "id": venue.id,
                "name": venue.name,
                "capacity": venue.capacity,
                "facilities": venue_facilities,
                "suitability": overall_suitability(checks),
                "checks": checks,
            }
        )

    return {
        "event_id": event.id,
        "title": event.title,
        "expected_attendance": event.expected_attendance,
        "required_facilities": required,
        "venues": results,
    }
