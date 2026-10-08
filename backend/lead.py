from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .assignment import AssignmentError, assign_event, get_active_coordinator, require_lead
from .auth import get_current_user
from .database import get_db
from .models import Event, User
from .status import DRAFT, RETURNED, SUBMITTED, UNDER_REVIEW

router = APIRouter(tags=["lead"])


class AssignmentRequest(BaseModel):
    coordinator_id: int


def enforce_lead(user: User) -> None:
    try:
        require_lead(user)
    except AssignmentError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error))


def event_row(event: Event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "organiser": event.organiser.username,
        "proposed_start": event.proposed_start,
        "expected_attendance": event.expected_attendance,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "assigned_coordinator": event.assigned_coordinator.username if event.assigned_coordinator else None,
        "submitted_at": event.submitted_at,
    }


@router.get("/lead/events")
def lead_events(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_lead(current_user)
    events = db.query(Event).filter(Event.status != DRAFT).order_by(Event.submitted_at.asc(), Event.id.asc()).all()
    return [event_row(event) for event in events]


@router.get("/lead/coordinators")
def active_coordinators(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_lead(current_user)
    users = db.query(User).filter(User.role.ilike("coordinator"), User.is_active_coordinator == True).order_by(User.username).all()
    return [{"id": user.id, "username": user.username} for user in users]


def apply_assignment(event_id: int, request: AssignmentRequest, current_user: User, db: Session, *, reassign: bool):
    enforce_lead(current_user)
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    assignable_statuses = {SUBMITTED, UNDER_REVIEW, RETURNED}
    if event.status not in assignable_statuses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only active submitted event requests can be assigned")
    try:
        coordinator = get_active_coordinator(db, request.coordinator_id)
        assign_event(db, event, coordinator, current_user, reassign=reassign)
    except AssignmentError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error))
    return event_row(event)


@router.post("/events/{event_id}/assign")
def initial_assignment(event_id: int, request: AssignmentRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply_assignment(event_id, request, current_user, db, reassign=False)


@router.post("/events/{event_id}/reassign")
def reassignment(event_id: int, request: AssignmentRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply_assignment(event_id, request, current_user, db, reassign=True)
