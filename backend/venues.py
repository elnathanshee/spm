"""Venues and Venue Management API.

SCRUM-30: Add a venue to the catalogue (Venue Staff).
SCRUM-31: Update or retire a venue record (Venue Staff).
Week 7 Customer Change #1: Setup time and turnaround time buffers.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Event, User, Venue, VenueBooking

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
) -> Tuple[bool, Optional[str]]:
    """AC1 & AC4: Check venue availability against existing bookings and buffers (Week 7 Change #1).

    Every booking occupies the venue from its start minus the venue's setup time
    to its end plus the venue's turnaround time. Two bookings conflict if those
    occupied windows overlap, so the buffers are applied to the requested slot
    as well as to each existing booking. Windows that only touch do not conflict.
    """
    bookings = (
        db.query(VenueBooking)
        .filter(
            VenueBooking.venue_id == venue.id,
            VenueBooking.status.in_(["Confirmed", "Approved", "Tentative", "Pending"]),
        )
        .all()
    )

    setup_delta = timedelta(minutes=venue.setup_time_mins or 0)
    turnaround_delta = timedelta(minutes=venue.turnaround_time_mins or 0)

    # The window the requested event would occupy, including its own buffers
    requested_start = start_time - setup_delta
    requested_end = end_time + turnaround_delta

    for b in bookings:
        b_start = b.booking_start
        b_end = b.booking_end

        # The window the existing booking occupies, including its buffers
        blocked_start = b_start - setup_delta
        blocked_end = b_end + turnaround_delta

        if requested_start < blocked_end and requested_end > blocked_start:
            booking_text = f"{b_start.strftime('%Y-%m-%d %H:%M')} - {b_end.strftime('%H:%M')}"
            if start_time < b_end and end_time > b_start:
                return False, f"Direct conflict with existing booking ({booking_text})"
            return (
                False,
                f"Conflicts with booking ({booking_text}) once {venue.setup_time_mins}m setup "
                f"and {venue.turnaround_time_mins}m turnaround are included",
            )

    return True, None


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

    # Offsets are dropped without conversion, as before (time zones are a
    # Sprint 3 follow-up). Doing it before the comparison below means a period
    # with an offset on one end only cannot crash that comparison.
    if start_time is not None and start_time.tzinfo:
        start_time = start_time.replace(tzinfo=None)
    if end_time is not None and end_time.tzinfo:
        end_time = end_time.replace(tzinfo=None)

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

# Booking statuses that hold a venue. The same list check_venue_availability
# uses inline; unifying the two is a follow-up.
BLOCKING_BOOKING_STATUSES = ["Confirmed", "Approved", "Tentative", "Pending"]

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


def booking_to_dict(booking: VenueBooking) -> Dict[str, Any]:
    return {
        "booking_id": booking.id,
        "event_id": booking.event_id,
        "event_title": booking.event.title,
        "booking_start": booking.booking_start.isoformat(),
        "booking_end": booking.booking_end.isoformat(),
        "status": booking.status,
    }


def booking_detail_dict(booking: VenueBooking) -> Dict[str, Any]:
    return {
        "booking_id": booking.id,
        "id": booking.id,
        "venue_id": booking.venue_id,
        "event_id": booking.event_id,
        "event_title": booking.event.title if booking.event else None,
        "booking_start": booking.booking_start.isoformat(),
        "booking_end": booking.booking_end.isoformat(),
        "status": booking.status,
        "rejection_reason": getattr(booking, "rejection_reason", None),
        "created_at": booking.created_at.isoformat() if hasattr(booking, "created_at") and booking.created_at else None,
    }


@router.patch("/{venue_id}")
def update_venue(
    venue_id: int,
    payload: VenueUpdate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """SCRUM-31 AC1: update any attribute of a venue. A retired venue can be
    corrected, but stays retired."""
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

    # Stored the same way create_venue stores them.
    for field, value in changes.items():
        if field in ("facilities", "supported_layouts"):
            value = serialize_list(value)
        elif field in ("accessibility", "operating_hours"):
            value = value.strip() if value else None
        setattr(venue, field, value)

    db.commit()
    db.refresh(venue)

    return {
        "message": "Venue updated successfully",
        "venue": venue_to_dict(venue),
        "updated_fields": list(changes),
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
            VenueBooking.status.in_(BLOCKING_BOOKING_STATUSES),
            VenueBooking.booking_end > now,
        )
        .order_by(VenueBooking.booking_start.asc())
        .all()
    )
    affected_bookings = [booking_to_dict(b) for b in affected]

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

    start = payload.booking_start.replace(tzinfo=None) if payload.booking_start.tzinfo else payload.booking_start
    end = payload.booking_end.replace(tzinfo=None) if payload.booking_end.tzinfo else payload.booking_end

    is_avail, reason = check_venue_availability(venue, start, end, db)
    if not is_avail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or f"Venue '{venue.name}' is unavailable for the requested time slot",
        )

    if payload.venue_requirements:
        event.venue_requirements = payload.venue_requirements

    booking = VenueBooking(
        venue_id=venue.id,
        event_id=event.id,
        booking_start=start,
        booking_end=end,
        status="Pending",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    return {
        "message": "Venue booking request submitted successfully",
        "booking": booking_detail_dict(booking),
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
    return [booking_detail_dict(b) for b in bookings]

