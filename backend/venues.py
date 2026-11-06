"""Venues and Venue Management API.

SCRUM-30: Add a venue to the catalogue (Venue Staff).
SCRUM-31: Update or retire a venue record (Venue Staff).
Week 7 Customer Change #1: Setup time and turnaround time buffers.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session, object_session

from .notifications import notify_booking, KIND_BOOKING_SUBMITTED, KIND_BOOKING_APPROVED, KIND_BOOKING_REJECTED
from sqlalchemy.exc import IntegrityError
from .auth import get_current_user
from .booking_recheck import active_conflict_flag, conflict_flag_record, recheck_venue_bookings  # [SCRUM-80 | Renee; GitHub #29]
from .booking_rules import BLOCKING_STATUSES, find_conflicts, is_blocking, windows_overlap  # [SCRUM-38 | Elnathan]
from .booking_rules import expired_hold_refusal, hold_fields  # [SCRUM-81 | Elnathan]
from .booking_rules import singapore_time, to_naive_utc  # [SCRUM-95]
from .hold_store import store_default_expiry  # [SCRUM-81 | Elnathan]
from .database import get_db
from .events import apply_booking_requirements  # [SCRUM-29]
from .models import Event, User, Venue, VenueBooking, VenueBookingConflictFlag, VenueBookingDecision

router = APIRouter(prefix="/venues", tags=["venues"])


# ---------------------------------------------------------------------------
# Role Guards
# ---------------------------------------------------------------------------

def require_venue_staff(current_user: User = Depends(get_current_user)) -> User:
    """SCRUM-30 AC3: Only Venue Staff can create venue records."""
    if (current_user.role or "").strip().lower() != "venue_staff":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Venue Staff can create or manage venue records",
        )
    return current_user


VENUE_SEARCH_ROLES = {"coordinator", "lead", "venue_staff"}


def require_venue_searcher(current_user: User = Depends(get_current_user)) -> User:
    """SCRUM-17 AC3: venue search is an internal planning function.

    Organisers and other external roles are refused, so they cannot see the
    booking times of other organisers' events in conflict_reason.
    """
    if (current_user.role or "").strip().lower() not in VENUE_SEARCH_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Coordinators, Leads and Venue Staff can search venues",
        )
    return current_user


def require_coordinator(current_user: User = Depends(get_current_user)) -> User:
    """SCRUM-35 AC4: Only Coordinators can submit venue booking requests.
    Organisers and external roles cannot select venues directly.
    """
    role = (current_user.role or "").strip().lower()
    if role not in ("coordinator", "lead"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Coordinators can submit venue booking requests",
        )
    return current_user


def require_booking_viewer(current_user: User = Depends(get_current_user)) -> User:
    """SCRUM-35 AC2: Venue Staff, Coordinators and Leads can view venue bookings."""
    role = (current_user.role or "").strip().lower()
    if role not in ("coordinator", "lead", "venue_staff"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Coordinators and Venue Staff can view venue bookings",
        )
    return current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def serialize_list(items: Optional[List[str]]) -> Optional[str]:
    if items is None:
        return None
    return json.dumps([str(i).strip() for i in items if str(i).strip()])


def deserialize_list(data: Optional[str]) -> List[str]:
    if not data:
        return []
    try:
        parsed = json.loads(data)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return [str(data)]
    except (json.JSONDecodeError, TypeError):
        return [item.strip() for item in data.split(",") if item.strip()]


def venue_to_dict(venue: Venue) -> Dict[str, Any]:
    return {
        "id": venue.id,
        "name": venue.name,
        "location": venue.location,
        "capacity": venue.capacity,
        "facilities": deserialize_list(venue.facilities),
        "accessibility": venue.accessibility,
        "supported_layouts": deserialize_list(venue.supported_layouts),
        "operating_hours": venue.operating_hours,
        "setup_time_mins": venue.setup_time_mins,
        "turnaround_time_mins": venue.turnaround_time_mins,
        "is_active": venue.is_active,
        "created_at": venue.created_at.isoformat() if venue.created_at else None,
    }


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class VenueCreate(BaseModel):
    name: str
    location: str
    capacity: int
    facilities: Optional[List[str]] = None
    accessibility: Optional[str] = None
    supported_layouts: Optional[List[str]] = None
    operating_hours: Optional[str] = None
    setup_time_mins: Optional[int] = 30
    turnaround_time_mins: Optional[int] = 45

    @field_validator("name", "location")
    @classmethod
    def check_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be blank")
        return v.strip()

    @field_validator("capacity")
    @classmethod
    def check_capacity(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Capacity must be greater than 0")
        return v

    @field_validator("setup_time_mins", "turnaround_time_mins")
    @classmethod
    def check_non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Buffer time cannot be negative")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED)
def create_venue(
    payload: VenueCreate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-30: Add a venue to the catalogue (Venue Staff only)."""
    existing = db.query(Venue).filter(func.lower(Venue.name) == payload.name.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A venue named '{payload.name}' already exists",
        )

    venue = Venue(
        name=payload.name,
        location=payload.location,
        capacity=payload.capacity,
        facilities=serialize_list(payload.facilities),
        accessibility=payload.accessibility.strip() if payload.accessibility else None,
        supported_layouts=serialize_list(payload.supported_layouts),
        operating_hours=payload.operating_hours.strip() if payload.operating_hours else None,
        setup_time_mins=payload.setup_time_mins if payload.setup_time_mins is not None else 30,
        turnaround_time_mins=payload.turnaround_time_mins if payload.turnaround_time_mins is not None else 45,
        is_active=True,
    )
    db.add(venue)
    db.commit()
    db.refresh(venue)

    return {
        "message": "Venue created successfully",
        "venue": venue_to_dict(venue),
    }


@router.get("")
def list_venues(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all active venues."""
    venues = db.query(Venue).filter(Venue.is_active == True).order_by(Venue.name.asc()).all()
    return [venue_to_dict(v) for v in venues]


def check_venue_availability(
    venue: Venue,
    start_time: datetime,
    end_time: datetime,
    db: Session,
    *,
    exclude_booking_id: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """AC1 & AC4: Check venue availability against existing bookings and buffers (Week 7 Change #1).

    Every booking occupies the venue from its start minus the venue's setup time
    to its end plus the venue's turnaround time. Two bookings conflict if those
    occupied windows overlap, so the buffers are applied to the requested slot
    as well as to each existing booking. Windows that only touch do not conflict.

    [SCRUM-38 | Elnathan] The rule itself is in booking_rules.find_conflicts.
    When several bookings clash, the earliest-starting one is named (then the
    lowest id); before SCRUM-38 the order was undefined.

    [SCRUM-38 part 2] exclude_booking_id leaves one booking out, so an existing
    booking can be checked against the venue's other bookings (at approval).
    """
    conflicts = find_conflicts(db, venue, start_time, end_time, exclude_booking_id=exclude_booking_id)
    if not conflicts:
        return True, None

    b = conflicts[0]
    # [SCRUM-95] Singapore time, in the same words as before.
    booking_text = f"{singapore_time(b.booking_start).strftime('%Y-%m-%d %H:%M')} - {singapore_time(b.booking_end).strftime('%H:%M')}"
    if windows_overlap(start_time, end_time, b.booking_start, b.booking_end):
        return False, f"Direct conflict with existing booking ({booking_text})"
    return (
        False,
        f"Conflicts with booking ({booking_text}) once {venue.setup_time_mins}m setup "
        f"and {venue.turnaround_time_mins}m turnaround are included",
    )


@router.get("/search")
def search_venues(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    min_capacity: Optional[int] = None,
    location: Optional[str] = None,
    accessibility: Optional[str] = None,
    layout: Optional[str] = None,
    facilities: Optional[str] = None,
    name: Optional[str] = None,
    available_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_venue_searcher),
):
    """SCRUM-33: Search and filter venues against event requirements."""
    # A period is given in full or not at all. Half a period used to be
    # silently ignored, which reported a booked venue as available.
    if (start_time is None) != (end_time is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide both start_time and end_time, or neither",
        )

    # [SCRUM-95] Offsets are converted to UTC (D7), as bookings are stored.
    # Doing it before the comparison below means a period with an offset on
    # one end only cannot crash that comparison.
    if start_time is not None:
        start_time = to_naive_utc(start_time)
    if end_time is not None:
        end_time = to_naive_utc(end_time)

    if start_time is not None and end_time <= start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    # Only active (non-retired) venues are searchable
    query = db.query(Venue).filter(Venue.is_active == True)

    if min_capacity is not None:
        query = query.filter(Venue.capacity >= min_capacity)

    if location and location.strip():
        query = query.filter(func.lower(Venue.location).contains(location.strip().lower()))

    if accessibility and accessibility.strip():
        query = query.filter(func.lower(Venue.accessibility).contains(accessibility.strip().lower()))

    if name and name.strip():
        query = query.filter(func.lower(Venue.name).contains(name.strip().lower()))

    venues = query.order_by(Venue.name.asc()).all()

    # In-memory filtering for JSON list attributes (layout, facilities)
    filtered_venues = []
    for v in venues:
        # Layout check
        if layout and layout.strip():
            v_layouts = [l.lower() for l in deserialize_list(v.supported_layouts)]
            if layout.strip().lower() not in v_layouts:
                continue

        # Facilities check (all required facilities must be present)
        if facilities and facilities.strip():
            req_facilities = [f.strip().lower() for f in facilities.split(",") if f.strip()]
            v_facilities = [f.lower() for f in deserialize_list(v.facilities)]
            if not all(rf in v_facilities for rf in req_facilities):
                continue

        filtered_venues.append(v)

    # Date/Time Availability & Buffer Checking
    results = []
    for v in filtered_venues:
        d = venue_to_dict(v)
        if start_time is not None:
            is_avail, reason = check_venue_availability(v, start_time, end_time, db)
            d["is_available"] = is_avail
            d["conflict_reason"] = reason
        else:
            d["is_available"] = True
            d["conflict_reason"] = None

        if available_only and not d["is_available"]:
            continue

        results.append(d)

    return results


@router.get("/{venue_id}")
def get_venue(
    venue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get venue by ID."""
    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_active == True).first()
    if not venue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Venue not found",
        )
    return venue_to_dict(venue)


# ---------------------------------------------------------------------------
# SCRUM-31: Update or retire a venue record (Venue Staff)
# ---------------------------------------------------------------------------

# These columns can't be empty, so an update can't set them to null.
REQUIRED_VENUE_FIELDS = ("name", "location", "capacity", "setup_time_mins", "turnaround_time_mins")


class VenueUpdate(VenueCreate):
    """Partial update. Inherits VenueCreate's validators, so an update follows
    the same rules as creating a venue (SCRUM-30). Every field is optional.
    Unknown fields, including is_active, are refused with 422."""

    model_config = {"extra": "forbid"}

    name: Optional[str] = None
    location: Optional[str] = None
    capacity: Optional[int] = None
    facilities: Optional[List[str]] = None
    accessibility: Optional[str] = None
    supported_layouts: Optional[List[str]] = None
    operating_hours: Optional[str] = None
    setup_time_mins: Optional[int] = None
    turnaround_time_mins: Optional[int] = None

    @field_validator(*REQUIRED_VENUE_FIELDS, mode="before")
    @classmethod
    def refuse_null(cls, v):
        if v is None:
            raise ValueError("Field cannot be null")
        return v


class VenueBookingRequest(BaseModel):
    event_id: int
    booking_start: datetime
    booking_end: datetime
    venue_requirements: Optional[str] = None

    @model_validator(mode="after")
    def validate_booking_dates(self) -> "VenueBookingRequest":
        start = self.booking_start
        end = self.booking_end
        now = datetime.now(timezone.utc) if start.tzinfo is not None else datetime.utcnow()
        if end <= start:
            raise ValueError("booking_end must be after booking_start")
        if start < now:
            raise ValueError("booking_start cannot be in the past")
        return self


# [SCRUM-80 | Renee] A clash found after the venue's setup/turnaround changed
def booking_to_dict(booking: VenueBooking) -> Dict[str, Any]:
    return {
        "booking_id": booking.id,
        "event_id": booking.event_id,
        "event_title": booking.event.title,
        "booking_start": booking.booking_start.isoformat(),
        "booking_end": booking.booking_end.isoformat(),
        "status": booking.status,
    }


def booking_detail_dict(
    booking: VenueBooking, candidates_by_venue: Optional[Dict[int, List[VenueBooking]]] = None
) -> Dict[str, Any]:
    """[GitHub #29, PR #35 review] For a list, pass one candidates_by_venue
    dict to every call, so each venue's bookings are loaded once."""
    decision_reason = booking.decision.reason if hasattr(booking, "decision") and booking.decision else None
    active_flag = active_conflict_flag(object_session(booking), booking, candidates_by_venue)
    return {
        "booking_id": booking.id,
        "id": booking.id,
        "venue_id": booking.venue_id,
        "event_id": booking.event_id,
        "event_title": booking.event.title if booking.event else None,
        "requester_id": booking.requester_id,
        # [SCRUM-89 | Renee] AC5: the requester by name (None if none was recorded)
        "requester_name": booking.requester.username if booking.requester else None,
        "booking_start": booking.booking_start.isoformat(),
        "booking_end": booking.booking_end.isoformat(),
        "status": booking.status,
        "rejection_reason": decision_reason or getattr(booking, "rejection_reason", None),
        "created_at": booking.created_at.isoformat() if hasattr(booking, "created_at") and booking.created_at else None,
        # [SCRUM-80 | Renee] [GitHub #29] the flag only while the clash still exists
        "conflict_flag": active_flag,
        # [GitHub #29] the stored flag as first raised, marked Active or Resolved
        "conflict_flag_record": conflict_flag_record(booking, active_flag),
        # [SCRUM-81] hold_expires_at and warning_minutes; replaces "status" with "Expired" once the hold expires
        **hold_fields(booking),
    }


@router.patch("/{venue_id}")
def update_venue(
    venue_id: int,
    payload: VenueUpdate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-31 AC1: update any attribute of a venue. A retired venue can be
    corrected, but stays retired.

    [SCRUM-80 | Renee] AC3: when setup or turnaround time changes, the venue's
    upcoming bookings are re-checked and any new clash is flagged (never
    removed). The flagged bookings are returned in flagged_bookings."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Send at least one field to update")

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")

    if "name" in changes:
        clash = (
            db.query(Venue)
            .filter(func.lower(Venue.name) == changes["name"].lower(), Venue.id != venue.id)
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A venue named '{changes['name']}' already exists",
            )

    buffers_before = (venue.setup_time_mins, venue.turnaround_time_mins)  # [SCRUM-80 | Renee]

    # Stored the same way create_venue stores them.
    for field, value in changes.items():
        if field in ("facilities", "supported_layouts"):
            value = serialize_list(value)
        elif field in ("accessibility", "operating_hours"):
            value = value.strip() if value else None
        setattr(venue, field, value)

    db.commit()
    db.refresh(venue)

    # [SCRUM-80 | Renee] AC3: re-check existing bookings only if a buffer changed
    flagged = []
    if (venue.setup_time_mins, venue.turnaround_time_mins) != buffers_before:
        flagged = recheck_venue_bookings(db, venue)
        db.commit()

    flagged_candidates: Dict[int, List[VenueBooking]] = {}  # [GitHub #29] load the venue's bookings once
    return {
        "message": "Venue updated successfully",
        "venue": venue_to_dict(venue),
        "updated_fields": list(changes),
        "flagged_bookings": [booking_detail_dict(b, flagged_candidates) for b in flagged],
    }


@router.post("/{venue_id}/retire")
def retire_venue(
    venue_id: int,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-31 AC2, AC3: retire a venue so it is no longer bookable.

    Nothing is deleted. Bookings that haven't ended yet and still hold the venue
    are listed as affected_bookings, but are not cancelled or changed.
    """
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    if not venue.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Venue '{venue.name}' is already retired",
        )

    # Naive UTC, as events.py compares naive times.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    affected = (
        db.query(VenueBooking)
        .filter(
            VenueBooking.venue_id == venue.id,
            VenueBooking.status.in_(BLOCKING_STATUSES),
            VenueBooking.booking_end > now,
        )
        .order_by(VenueBooking.booking_start.asc())
        .all()
    )
    # [SCRUM-38] The SQL status filter is a first cut; is_blocking has the final say.
    affected_bookings = [booking_to_dict(b) for b in affected if is_blocking(b)]

    venue.is_active = False
    db.commit()
    db.refresh(venue)

    return {
        "message": "Venue retired successfully",
        "venue": venue_to_dict(venue),
        "affected_bookings": affected_bookings,
    }


@router.post("/{venue_id}/bookings", status_code=status.HTTP_201_CREATED)
def submit_venue_booking_request(
    venue_id: int,
    payload: VenueBookingRequest,
    current_user: User = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """SCRUM-35: Submit a venue booking request (Coordinator only).

    Creates a tentative hold (status='Pending') on the venue if available.
    Customer constraint: at most one active tentative hold OR one confirmed
    booking per (venue, time slot). Overlaps return 409 Conflict.
    """
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    if not venue.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Venue '{venue.name}' is retired and cannot be booked",
        )

    event = db.query(Event).filter(Event.id == payload.event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    role = (current_user.role or "").strip().lower()
    if role == "coordinator" and event.assigned_coordinator_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coordinators can book venues only for events assigned to them",
        )

    # [SCRUM-95] D7: stored in UTC, like hold times, so the expiry cap at the
    # booking's start compares like with like.
    start = to_naive_utc(payload.booking_start)
    end = to_naive_utc(payload.booking_end)

    is_avail, reason = check_venue_availability(venue, start, end, db)
    if not is_avail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or f"Venue '{venue.name}' is unavailable for the requested time slot",
        )

    # [SCRUM-29] Not a plain write: new requirements are an important change.
    arrangements_flagged = apply_booking_requirements(db, event, current_user, payload.venue_requirements)

    booking = VenueBooking(
        venue_id=venue.id,
        event_id=event.id,
        requester_id=current_user.id,
        booking_start=start,
        booking_end=end,
        status="Pending",
    )
    db.add(booking)
    db.flush()  # Obtain the booking ID before storing its hold and notifications.
    store_default_expiry(db, booking)
    notify_booking(db, booking, KIND_BOOKING_SUBMITTED)
    db.commit()
    db.refresh(booking)

    return {
        "message": "Venue booking request submitted successfully",
        "booking": booking_detail_dict(booking),
        "arrangements_flagged": arrangements_flagged,  # [SCRUM-29]
    }


@router.get("/{venue_id}/bookings")
def list_venue_bookings(
    venue_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(require_booking_viewer),
    db: Session = Depends(get_db),
):
    """SCRUM-35 AC2: View venue bookings / requests pending decision."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")

    query = db.query(VenueBooking).filter(VenueBooking.venue_id == venue_id)
    if status_filter:
        query = query.filter(VenueBooking.status == status_filter)
    bookings = query.order_by(VenueBooking.booking_start.asc()).all()
    candidates: Dict[int, List[VenueBooking]] = {}  # [GitHub #29] load the venue's bookings once
    return [booking_detail_dict(b, candidates) for b in bookings]


# ---------------------------------------------------------------------------
# [SCRUM-36 | Renee] Approve or reject a venue booking request (Venue Staff)
# ---------------------------------------------------------------------------

def decision_to_dict(decision: Optional[VenueBookingDecision]) -> Optional[Dict[str, Any]]:
    if decision is None:
        return None
    return {
        "decision": decision.decision,
        "decided_by": decision.decided_by.username if decision.decided_by else None,
        "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
        "reason": decision.reason,
    }


def booking_review_dict(booking: VenueBooking) -> Dict[str, Any]:
    """AC1: the request alongside the venue's capacity, layout, accessibility
    and facilities, plus the decision once one has been made."""
    data = booking_detail_dict(booking)
    data["venue"] = venue_to_dict(booking.venue) if booking.venue else None
    data["decision"] = decision_to_dict(booking.decision)
    return data


def _get_booking_or_404(booking_id: int, db: Session) -> VenueBooking:
    booking = db.query(VenueBooking).filter(VenueBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return booking


# [SCRUM-37 | Sarah] NEW: request body for /venues/bookings/{id}/reject.
# Reason is required and cannot be blank (missing/blank reason -> 422).
class RejectBookingAction(BaseModel):
    """SCRUM-37: rejecting a venue booking request must always include a reason."""

    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("A rejection reason is required")
        return v.strip()


def _decide_booking(
    booking_id: int,
    decision: str,
    new_status: str,
    staff: User,
    db: Session,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """AC2-AC4: record an explicit decision by a named Venue Staff member.

    Only Pending requests can be decided, and only once - a decided booking
    returns 409 and is left unchanged. Approve -> Confirmed (still holds the
    slot); Reject -> Rejected (frees the slot, the Coordinator sees it on
    GET /events/{id}/bookings).
    """
    booking = _get_booking_or_404(booking_id, db)
    approving = decision == "Approved"
    if approving:
        # [SCRUM-38 part 2, PR 27 review] Lock the venue row, so approvals at one
        # venue run one at a time, then re-read the booking and its hold: what this
        # request read before the lock may be out of date. FOR UPDATE does nothing
        # on SQLite; there the tests exercise the re-check and the conditional
        # update below. On Neon (Postgres) the lock is what serializes approvals.
        db.query(Venue).filter(Venue.id == booking.venue_id).with_for_update().one()
        db.expire_all()
    refusal = expired_hold_refusal(booking)  # [SCRUM-81] an expired hold can't be decided
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
    if booking.status != "Pending" or booking.decision is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Booking has already been decided (status: {booking.status})",
        )
    # [SCRUM-38 part 2] AC2-AC4: approving re-checks clashes with the venue's
    # current setup and turnaround. Reject is never blocked: it is how Venue
    # Staff resolve a clash (D6).
    if approving:
        available, clash = check_venue_availability(
            booking.venue, booking.booking_start, booking.booking_end, db, exclude_booking_id=booking.id
        )
        if not available:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot approve: {clash}")
        # Confirm only while the booking is still Pending. If another request got
        # there first (it expired, or was decided), nothing changes: 409.
        confirmed = (
            db.query(VenueBooking)
            .filter(VenueBooking.id == booking.id, VenueBooking.status == "Pending")
            .update({VenueBooking.status: new_status}, synchronize_session=False)
        )
        db.expire(booking)
        if not confirmed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=expired_hold_refusal(booking) or f"Booking has already been decided (status: {booking.status})",
            )
    else:
        booking.status = new_status
    booking.decision = VenueBookingDecision(
        decision=decision,
        decided_by_id=staff.id,
        reason=reason,
    )
    try:
        db.flush()  # The unique decision constraint also arbitrates concurrent decisions.
        notify_booking(db, booking, KIND_BOOKING_APPROVED if decision == "Approved" else KIND_BOOKING_REJECTED)
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(VenueBookingDecision).filter_by(booking_id=booking_id).first():
            raise HTTPException(status_code=409, detail="Booking has already been decided")
        raise
    db.refresh(booking)

    return {
        "message": f"Booking request {decision.lower()}",
        "booking": booking_review_dict(booking),
    }


@router.get("/bookings/{booking_id}")
def get_booking_for_review(
    booking_id: int,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-36 AC1: Venue Staff view a request with the venue's details."""
    return booking_review_dict(_get_booking_or_404(booking_id, db))


@router.post("/bookings/{booking_id}/approve")
def approve_booking(
    booking_id: int,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-36 AC2, AC4: approve a Pending request -> Confirmed booking."""
    return _decide_booking(booking_id, "Approved", "Confirmed", current_user, db)


@router.post("/bookings/{booking_id}/reject")
def reject_booking(
    booking_id: int,
    action: RejectBookingAction,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-36 AC3, AC4 & SCRUM-37 AC1, AC2: reject a Pending request with mandatory reason."""
    return _decide_booking(booking_id, "Rejected", "Rejected", current_user, db, reason=action.reason)
