"""[Sprint 5] The equipment Technical Support hold, and who has reserved it.

SCRUM-41: a catalogue of equipment items. Technical Support only.
SCRUM-42: update an item, or record a number of units unavailable for a period.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import get_current_user
from .booking_rules import to_naive_utc
from .database import get_db
from .equipment import TECHNICAL_SUPPORT, _role_of
from .models import EquipmentItem, EquipmentItemUnavailability, User

router = APIRouter(tags=["equipment catalogue"])

# [SCRUM-41] The Integer column's limit. Above it Postgres fails with a 500, so
# the request body refuses it with a 422 first.
MAX_QUANTITY = 2147483647


def require_technical_support(current_user: User = Depends(get_current_user)) -> User:
    """[SCRUM-41] Only Technical Support manage the catalogue."""
    if _role_of(current_user) not in TECHNICAL_SUPPORT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Technical Support Staff can manage the equipment catalogue",
        )
    return current_user


def _required_text(value: str, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} cannot be empty")
    return value.strip()


def _optional_text(value: Optional[str]) -> Optional[str]:
    # Blank is stored as "not recorded", the same as equipment.py.
    if value is None or not value.strip():
        return None
    return value.strip()


class EquipmentItemCreate(BaseModel):
    """[SCRUM-41] The lengths match the columns, so a long value gets a 422 on Postgres too."""

    equipment_type: str = Field(..., max_length=100)
    name: str = Field(..., max_length=255)
    total_quantity: int = Field(..., ge=1, le=MAX_QUANTITY)
    notes: Optional[str] = None

    @field_validator("equipment_type")
    @classmethod
    def clean_type(cls, value: str) -> str:
        return _required_text(value, "Equipment type")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value, "Name")

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)


def item_to_dict(item: EquipmentItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "equipment_type": item.equipment_type,
        "name": item.name,
        "total_quantity": item.total_quantity,
        "notes": item.notes,
    }


def _refuse_duplicate(db: Session, equipment_type: str, name: str, exclude_id: Optional[int] = None) -> None:
    """[SCRUM-41] Two rows for one item would split its units, so availability would be wrong.
    Case is ignored, as with equipment request lines (SCRUM-39)."""
    query = db.query(EquipmentItem).filter(
        func.lower(EquipmentItem.equipment_type) == equipment_type.lower(),
        func.lower(EquipmentItem.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(EquipmentItem.id != exclude_id)
    if query.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{name}' is already in the catalogue as {equipment_type}. Update that item instead.",
        )


@router.get("/equipment/items")
def list_items(
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-41] The catalogue, by type then name."""
    items = db.query(EquipmentItem).order_by(EquipmentItem.equipment_type, EquipmentItem.name).all()
    return {"items": [item_to_dict(item) for item in items]}


@router.post("/equipment/items", status_code=status.HTTP_201_CREATED)
def add_item(
    payload: EquipmentItemCreate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-41] Add one item to the catalogue."""
    _refuse_duplicate(db, payload.equipment_type, payload.name)
    item = EquipmentItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"message": "Equipment item added", "item": item_to_dict(item)}


# ---------------------------------------------------------------------------
# SCRUM-42: update an item, record units unavailable for a period
# ---------------------------------------------------------------------------


class EquipmentItemUpdate(BaseModel):
    """[SCRUM-42] Only the fields sent change. Notes can be cleared, but the
    required columns can't be set to null, or the save would be a 500."""

    equipment_type: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    total_quantity: Optional[int] = Field(None, ge=1, le=MAX_QUANTITY)
    notes: Optional[str] = None

    @field_validator("equipment_type")
    @classmethod
    def clean_type(cls, value: Optional[str]) -> str:
        return _required_text(value, "Equipment type")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> str:
        return _required_text(value, "Name")

    @field_validator("total_quantity")
    @classmethod
    def total_is_present(cls, value: Optional[int]) -> int:
        if value is None:
            raise ValueError("Total quantity cannot be empty")
        return value

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)


class UnavailabilityCreate(BaseModel):
    units: int = Field(..., ge=1, le=MAX_QUANTITY)
    start_at: datetime
    end_at: datetime
    reason: Optional[str] = None

    @field_validator("start_at", "end_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        # A time at the very edge of the calendar can't be moved to UTC.
        try:
            return to_naive_utc(value)
        except OverflowError:
            raise ValueError("That date is out of range")

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: Optional[str]) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def ends_after_it_starts(self):
        if self.end_at <= self.start_at:
            raise ValueError("The period must end after it starts")
        return self


def period_to_dict(period: EquipmentItemUnavailability) -> Dict[str, Any]:
    return {
        "id": period.id,
        "item_id": period.item_id,
        "units": period.units,
        "start_at": period.start_at.isoformat(),
        "end_at": period.end_at.isoformat(),
        "reason": period.reason,
    }


def load_item(db: Session, item_id: int) -> EquipmentItem:
    item = db.query(EquipmentItem).filter(EquipmentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment item not found")
    return item


@router.patch("/equipment/items/{item_id}")
def update_item(
    item_id: int,
    payload: EquipmentItemUpdate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Update an item. A total below what's reserved is allowed, because
    units really can be lost: existing reservations stay and availability shows 0."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Send at least one field to update")
    item = load_item(db, item_id)
    _refuse_duplicate(
        db, changes.get("equipment_type", item.equipment_type), changes.get("name", item.name), exclude_id=item.id
    )
    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return {"message": "Equipment item updated", "item": item_to_dict(item)}


@router.get("/equipment/items/{item_id}/unavailability")
def list_unavailability(
    item_id: int,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] The item's unavailable periods, soonest first."""
    item = load_item(db, item_id)
    periods = (
        db.query(EquipmentItemUnavailability)
        .filter(EquipmentItemUnavailability.item_id == item.id)
        .order_by(EquipmentItemUnavailability.start_at, EquipmentItemUnavailability.id)
        .all()
    )
    return {"item_id": item.id, "periods": [period_to_dict(period) for period in periods]}


@router.post("/equipment/items/{item_id}/unavailability", status_code=status.HTTP_201_CREATED)
def record_unavailability(
    item_id: int,
    payload: UnavailabilityCreate,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Record a number of units as unavailable for a period."""
    item = load_item(db, item_id)
    if payload.units > item.total_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This item only has {item.total_quantity} units in total, so {payload.units} can't be unavailable.",
        )
    period = EquipmentItemUnavailability(item_id=item.id, created_by_id=current_user.id, **payload.model_dump())
    db.add(period)
    db.commit()
    db.refresh(period)
    return {"message": "Unavailable units recorded", "period": period_to_dict(period)}


@router.delete("/equipment/items/{item_id}/unavailability/{period_id}")
def remove_unavailability(
    item_id: int,
    period_id: int,
    current_user: User = Depends(require_technical_support),
    db: Session = Depends(get_db),
):
    """[SCRUM-42] Remove a period, for example when units come back early."""
    period = (
        db.query(EquipmentItemUnavailability)
        .filter(EquipmentItemUnavailability.id == period_id, EquipmentItemUnavailability.item_id == item_id)
        .first()
    )
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unavailable period not found")
    db.delete(period)
    db.commit()
    return {"message": "Unavailable period removed"}
