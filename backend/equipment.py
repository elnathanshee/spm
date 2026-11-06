"""Equipment requirements for an event.

SCRUM-39: Record equipment requirements for an event (Coordinator).

Acceptance criteria:
  AC1  The record captures equipment type, quantity and relevant technical
       requirements.
  AC2  The request is visible to Technical Support Staff.
  AC3  The request is associated with a specific event and its date and time.

Each line item is one equipment type with a quantity, so an event's needs read
as a list rather than a paragraph. Technical Support Staff read them, either
for one event or as a worklist across every event, with each line carrying the
event it belongs to and when that event is due.

Two reading rules, which differ on purpose:
  * One event's list follows SCRUM-18, where a Coordinator may view any event.
  * The worklist is "work waiting on me", so a Coordinator sees only the events
    assigned to them, while a Lead and Technical Support Staff see all of them.

Recording is a Coordinator action, because AC2 makes Technical Support the
audience for these requests rather than their author. Reviewing and changing a
request is SCRUM-40.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import EquipmentRequest, Event, User
from .status import CANCELLED, COMPLETED

router = APIRouter(tags=["equipment"])

COORDINATOR = "coordinator"
LEAD = "lead"
# auth.py accepts both spellings of the Technical Support role.
TECHNICAL_SUPPORT = {"technical support", "technical_support"}

RECORDER_ROLES = {COORDINATOR, LEAD}
VIEWER_ROLES = RECORDER_ROLES | TECHNICAL_SUPPORT


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


# ---------------------------------------------------------------------------
# Role guards
# ---------------------------------------------------------------------------


def require_equipment_recorder(current_user: User = Depends(get_current_user)) -> User:
    """AC2: recording is a Coordinator action. A Lead may act on any event."""
    if _role_of(current_user) not in RECORDER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Coordinators can record equipment requirements",
        )
    return current_user


def require_equipment_viewer(current_user: User = Depends(get_current_user)) -> User:
    """AC2: Technical Support Staff read these, alongside Coordinators and Leads."""
    if _role_of(current_user) not in VIEWER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Coordinators, Leads and Technical Support Staff can view equipment requirements",
        )
    return current_user


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class EquipmentRequestCreate(BaseModel):
    """One equipment line item.

    ``quantity`` is a whole number above zero: a request for none of something
    is not a request. ``technical_requirements`` is optional, because plenty of
    equipment needs no special note.
    """

    equipment_type: str
    quantity: int = Field(..., gt=0, description="How many are needed. A whole number above zero.")
    technical_requirements: Optional[str] = None

    @field_validator("equipment_type")
    @classmethod
    def clean_equipment_type(cls, value: str) -> str:
        if value is None or not value.strip():
            raise ValueError("Equipment type cannot be empty")
        return value.strip()

    @field_validator("technical_requirements")
    @classmethod
    def clean_technical_requirements(cls, value: Optional[str]) -> Optional[str]:
        # Blank notes are stored as "not recorded" rather than an empty string,
        # so a later reader can tell the difference.
        if value is None or not value.strip():
            return None
        return value.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def equipment_request_to_dict(request: EquipmentRequest) -> Dict[str, Any]:
    """AC3: every line carries its event and when that event is due."""
    event = request.event
    proposed_start = event.proposed_start if event else None
    return {
        "id": request.id,
        "event_id": request.event_id,
        "event_title": event.title if event else None,
        "event_proposed_start": proposed_start.isoformat() if proposed_start else None,
        "equipment_type": request.equipment_type,
        "quantity": request.quantity,
        "technical_requirements": request.technical_requirements,
        "requested_by": request.requested_by.username if request.requested_by else None,
        "created_at": request.created_at.isoformat() if request.created_at else None,
    }


def load_event(db: Session, event_id: int) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def by_event_date(request: EquipmentRequest):
    """Soonest event first, so the worklist is ordered by urgency.

    An event with no date yet sorts last rather than first, which is what
    ascending order would otherwise do with an empty value.
    """
    event = request.event
    start = event.proposed_start if event else None
    return (start is None, start or datetime.max, request.event_id, request.id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/events/{event_id}/equipment-requests", status_code=status.HTTP_201_CREATED)
def record_equipment_requirement(
    event_id: int,
    payload: EquipmentRequestCreate,
    current_user: User = Depends(require_equipment_recorder),
    db: Session = Depends(get_db),
):
    """SCRUM-39 AC1, AC3: record one piece of equipment an event needs."""
    event = load_event(db, event_id)

    # Week 7 Change #5: a Coordinator works only on their own events. A Lead
    # covers any event, as in SCRUM-34 and SCRUM-35.
    if _role_of(current_user) == COORDINATOR and event.assigned_coordinator_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coordinators can record equipment only for events assigned to them",
        )
    # [SCRUM-102] Nothing can be arranged for a Cancelled or Completed event.
    if event.status in (CANCELLED, COMPLETED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This event is {event.status}, so it can't take new equipment.",
        )

    # One line per equipment type, so the list stays readable and quantities
    # are not split across rows. Case and padding are ignored when comparing.
    existing = (
        db.query(EquipmentRequest)
        .filter(
            EquipmentRequest.event_id == event.id,
            func.lower(EquipmentRequest.equipment_type) == payload.equipment_type.lower(),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{existing.equipment_type}' is already recorded for this event "
                f"(quantity {existing.quantity}). Amend that entry instead of adding a second one."
            ),
        )

    request = EquipmentRequest(
        event_id=event.id,
        equipment_type=payload.equipment_type,
        quantity=payload.quantity,
        technical_requirements=payload.technical_requirements,
        requested_by_id=current_user.id,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    return {
        "message": "Equipment requirement recorded successfully",
        "equipment_request": equipment_request_to_dict(request),
    }


@router.get("/events/{event_id}/equipment-requests")
def list_event_equipment_requirements(
    event_id: int,
    current_user: User = Depends(require_equipment_viewer),
    db: Session = Depends(get_db),
):
    """SCRUM-39 AC2, AC3: everything one event needs, in the order recorded."""
    event = load_event(db, event_id)

    requests: List[EquipmentRequest] = (
        db.query(EquipmentRequest)
        .filter(EquipmentRequest.event_id == event.id)
        .order_by(EquipmentRequest.id.asc())
        .all()
    )

    return {
        "event_id": event.id,
        "event_title": event.title,
        "event_proposed_start": event.proposed_start.isoformat() if event.proposed_start else None,
        "equipment_requests": [equipment_request_to_dict(r) for r in requests],
    }


@router.get("/equipment-requests")
def list_all_equipment_requirements(
    current_user: User = Depends(require_equipment_viewer),
    db: Session = Depends(get_db),
):
    """SCRUM-39 AC2: the Technical Support worklist, across every event.

    A Coordinator sees only the events assigned to them; a Lead and Technical
    Support Staff see all of them.
    """
    query = db.query(EquipmentRequest).join(Event, EquipmentRequest.event_id == Event.id)

    if _role_of(current_user) == COORDINATOR:
        query = query.filter(Event.assigned_coordinator_id == current_user.id)

    requests = sorted(query.all(), key=by_event_date)

    return {"equipment_requests": [equipment_request_to_dict(r) for r in requests]}
