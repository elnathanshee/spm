"""[Sprint 5] The equipment Technical Support hold, and who has reserved it.

SCRUM-41: a catalogue of equipment items. Technical Support only.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .equipment import TECHNICAL_SUPPORT, _role_of
from .models import EquipmentItem, User

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
