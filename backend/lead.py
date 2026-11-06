from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .assignment import AssignmentError, assign_event, get_active_coordinator, require_lead
from .auth import get_current_user
from .database import get_db
from .models import Event, User
from .status import (
    APPROVED,
    CANCELLED,
    COMPLETED,
    CONFIRMED,
    DRAFT,
    PLANNING,
    REJECTED,
    RETURNED,
    SAFETY_REVIEW,
    SUBMITTED,
    UNDER_REVIEW,
)

router = APIRouter(tags=["lead"])

# [SCRUM-85] A first assignment happens only while the request is still in review.
ASSIGNABLE_STATUSES = {SUBMITTED, UNDER_REVIEW, RETURNED}
# [SCRUM-85] A Coordinator can be replaced at any point until the event is finished.
REASSIGNABLE_STATUSES = ASSIGNABLE_STATUSES | {APPROVED, PLANNING, SAFETY_REVIEW, CONFIRMED}
# [SCRUM-85] What the Lead's list can hold: everything except a Draft.
LEAD_LIST_STATUSES = REASSIGNABLE_STATUSES | {REJECTED, COMPLETED, CANCELLED}


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
        # [SCRUM-85] /lead/coordinators lists active people only, so the row says when this one has left.
        "assigned_coordinator_active": event.assigned_coordinator.is_active_coordinator if event.assigned_coordinator else None,
        "submitted_at": event.submitted_at,
    }


@router.get("/lead/events")
def lead_events(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    coordinator_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce_lead(current_user)
    # [SCRUM-85] A mistyped filter says so, rather than looking like an empty list.
    if status_filter is not None and status_filter not in LEAD_LIST_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown status filter: {status_filter}")
    query = db.query(Event).filter(Event.status != DRAFT)
    if status_filter is not None:
        query = query.filter(Event.status == status_filter)
    if coordinator_id is not None:
        query = query.filter(Event.assigned_coordinator_id == coordinator_id)
    events = query.order_by(Event.submitted_at.asc(), Event.id.asc()).all()
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
    if reassign and event.status not in REASSIGNABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"A {event.status} event can't be reassigned")
    if not reassign and event.status not in ASSIGNABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only active submitted event requests can be assigned")
    try:
        coordinator = get_active_coordinator(db, request.coordinator_id)
        previous_id = event.assigned_coordinator_id
        assign_event(db, event, coordinator, current_user, reassign=reassign)
    except AssignmentError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error))
    row = event_row(event)
    row["previous_coordinator_id"] = previous_id
    row["reassigned_by"] = current_user.username
    return row


@router.post("/events/{event_id}/assign")
def initial_assignment(event_id: int, request: AssignmentRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply_assignment(event_id, request, current_user, db, reassign=False)


@router.post("/events/{event_id}/reassign")
def reassignment(event_id: int, request: AssignmentRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply_assignment(event_id, request, current_user, db, reassign=True)
