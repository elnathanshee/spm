"""Venues and Venue Management API.

SCRUM-30: Add a venue to the catalogue (Venue Staff).
Week 7 Customer Change #1: Setup time and turnaround time buffers.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import User, Venue

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
    existing = db.query(Venue).filter(Venue.name.ilike(payload.name)).first()
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
