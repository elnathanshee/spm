"""SCRUM-82: A venue becomes unavailable after booking (Week 7 customer change 2).

  GET    /venues/{id}/unavailability              Coordinators, Leads, Venue Staff
  POST   /venues/{id}/unavailability              Venue Staff record a period
  DELETE /venues/{id}/unavailability/{period_id}  Venue Staff remove a period

The blocking rule itself is booking_rules.unavailability_overlaps, so the
availability check, the flag and the calendar all use one rule.
"""

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session

from .booking_rules import to_naive_utc
from .database import get_db
from .models import User, Venue, VenueUnavailability
from .venues import require_venue_searcher, require_venue_staff

router = APIRouter(prefix="/venues", tags=["venue unavailability"])


class UnavailabilityCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()

    @model_validator(mode="after")
    def end_after_start(self) -> "UnavailabilityCreate":
        # [SCRUM-82] Convert first: one end with an offset and one without
        # can't be compared, and that would be a crash rather than a 422.
        self.start_at = to_naive_utc(self.start_at)
        self.end_at = to_naive_utc(self.end_at)
        if self.end_at <= self.start_at:
            raise ValueError("The end must be after the start")
        return self


def period_to_dict(period: VenueUnavailability) -> Dict[str, Any]:
    return {
        "id": period.id,
        "venue_id": period.venue_id,
        "start_at": period.start_at.isoformat(),
        "end_at": period.end_at.isoformat(),
        "reason": period.reason,
        "created_at": period.created_at.isoformat() if period.created_at else None,
    }


def _get_venue_or_404(venue_id: int, db: Session) -> Venue:
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    return venue


@router.get("/{venue_id}/unavailability")
def list_unavailability(
    venue_id: int,
    current_user: User = Depends(require_venue_searcher),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] The venue's periods, earliest first."""
    venue = _get_venue_or_404(venue_id, db)
    periods = (
        db.query(VenueUnavailability)
        .filter(VenueUnavailability.venue_id == venue.id)
        .order_by(VenueUnavailability.start_at.asc(), VenueUnavailability.id.asc())
        .all()
    )
    return [period_to_dict(period) for period in periods]


@router.post("/{venue_id}/unavailability", status_code=status.HTTP_201_CREATED)
def record_unavailability(
    venue_id: int,
    payload: UnavailabilityCreate,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] Record a period. The same period twice is refused (409), so a
    double submit can't notify anyone twice."""
    venue = _get_venue_or_404(venue_id, db)
    duplicate = (
        db.query(VenueUnavailability)
        .filter(
            VenueUnavailability.venue_id == venue.id,
            VenueUnavailability.start_at == payload.start_at,
            VenueUnavailability.end_at == payload.end_at,
            VenueUnavailability.reason == payload.reason,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This period is already recorded for the venue")

    period = VenueUnavailability(
        venue_id=venue.id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        reason=payload.reason,
        created_by_id=current_user.id,
    )
    db.add(period)
    db.commit()
    db.refresh(period)
    return {"message": "Unavailability recorded", "period": period_to_dict(period)}


@router.delete("/{venue_id}/unavailability/{period_id}")
def remove_unavailability(
    venue_id: int,
    period_id: int,
    current_user: User = Depends(require_venue_staff),
    db: Session = Depends(get_db),
):
    """[SCRUM-82] Remove a period, through its own venue only."""
    period = (
        db.query(VenueUnavailability)
        .filter(VenueUnavailability.id == period_id, VenueUnavailability.venue_id == venue_id)
        .first()
    )
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unavailability period not found")
    db.delete(period)
    db.commit()
    return {"message": "Unavailability removed"}
