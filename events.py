from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .assignment import NoCoordinatorAvailable, assign_coordinator_on_submission
from .auth import get_current_user
from .database import get_db
from .models import Event, EventStatusHistory, User
from .status import APPROVED, DRAFT, REJECTED, RETURNED, SUBMITTED, UNDER_REVIEW
from .status_rules import StatusChangeError, change_status

router = APIRouter(prefix="/events", tags=["events"])


def apply_status_change(
    db: Session,
    event: Event,
    new_status: str,
    actor: User,
    reason: Optional[str] = None,
):
    """Change an event's status through the SCRUM-27 rules.

    The rules decide whether the move is allowed and record who made it. A
    refusal is turned into the matching HTTP response here, so the rules
    themselves stay free of any web framework.
    """
    try:
        return change_status(db, event, new_status, actor, reason)
    except StatusChangeError as refusal:
        raise HTTPException(status_code=refusal.http_status, detail=str(refusal))


class ReviewAction(BaseModel):
    reason: Optional[str] = None


class EventCreate(BaseModel):
    title: str
    purpose: Optional[str] = None
    description: Optional[str] = None
    proposed_start: Optional[datetime] = None
    expected_attendance: Optional[int] = None
    venue_requirements: Optional[str] = None
    accessibility_needs: Optional[str] = None
    equipment_requirements: Optional[str] = None
    registration_needs: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Title cannot be empty")
        return v.strip()


def validate_event_business_rules(
    proposed_start: Optional[datetime],
    expected_attendance: Optional[int],
):
    if expected_attendance is not None and expected_attendance <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected attendance must be greater than 0",
        )

    if proposed_start is not None:
        now = datetime.now(timezone.utc)
        start_time = proposed_start
        if start_time.tzinfo is None:
            now = datetime.utcnow()
        if start_time < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Proposed start time cannot be in the past",
            )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_event_draft(
    event_in: EventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() != "organiser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organisers can create event requests",
        )

    validate_event_business_rules(
        event_in.proposed_start,
        event_in.expected_attendance,
    )

    event = Event(
        title=event_in.title,
        purpose=event_in.purpose,
        description=event_in.description,
        proposed_start=event_in.proposed_start,
        expected_attendance=event_in.expected_attendance,
        venue_requirements=event_in.venue_requirements,
        accessibility_needs=event_in.accessibility_needs,
        equipment_requirements=event_in.equipment_requirements,
        registration_needs=event_in.registration_needs,
        organiser_id=current_user.id,
        status=DRAFT,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Record initial status history
    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=None,
        new_status=DRAFT,
        actor_id=current_user.id,
        reason="Initial draft created",
    )
    db.add(status_history)
    db.commit()

    return {
        "id": event.id,
        "title": event.title,
        "purpose": event.purpose,
        "description": event.description,
        "proposed_start": event.proposed_start,
        "expected_attendance": event.expected_attendance,
        "venue_requirements": event.venue_requirements,
        "accessibility_needs": event.accessibility_needs,
        "equipment_requirements": event.equipment_requirements,
        "registration_needs": event.registration_needs,
        "organiser_id": event.organiser_id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "created_at": event.created_at,
    }


@router.post("/{event_id}/submit")
def submit_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() != "organiser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organisers can submit event requests",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.organiser_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to submit this event request",
        )

    # Re-check mandatory fields and rules
    if not event.title or not event.title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event title is required for submission",
        )

    validate_event_business_rules(
        event.proposed_start,
        event.expected_attendance,
    )

    # SCRUM-27: the rules decide whether this move is allowed (a Draft or a
    # Returned request may be submitted, nothing else) and record who did it.
    apply_status_change(
        db,
        event,
        SUBMITTED,
        current_user,
        reason="Event request submitted by organiser",
    )

    # Only assign a coordinator if one is not already assigned
    if not event.assigned_coordinator_id:
        try:
            assign_coordinator_on_submission(event, db)
        except NoCoordinatorAvailable as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "submitted_at": event.submitted_at,
    }


@router.post("/{event_id}/return")
def return_event_for_clarification(
    event_id: int,
    action: Optional[ReviewAction] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() != "coordinator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only coordinators can return event requests for clarification",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    reason_text = (action.reason if action and action.reason else "Returned to organiser for clarification")

    apply_status_change(db, event, RETURNED, current_user, reason=reason_text)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "clarification_reason": reason_text,
    }


@router.post("/{event_id}/review")
def start_review(
    event_id: int,
    action: Optional[ReviewAction] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SCRUM-27: the assigned Coordinator marks that review has started.

    Nothing happens automatically, so this is a deliberate action the
    Coordinator takes. It is what makes "awaiting first review" meaningful on
    the Coordinator's list of requests (SCRUM-24).
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    reason_text = (action.reason if action and action.reason else "Review started by coordinator")

    apply_status_change(db, event, UNDER_REVIEW, current_user, reason=reason_text)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
    }


@router.post("/{event_id}/approve")
def approve_event(
    event_id: int,
    action: Optional[ReviewAction] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() != "coordinator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only coordinators can approve event requests",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    reason_text = (action.reason if action and action.reason else "Event request approved by coordinator")

    apply_status_change(db, event, APPROVED, current_user, reason=reason_text)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
    }


@router.post("/{event_id}/reject")
def reject_event(
    event_id: int,
    action: Optional[ReviewAction] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() != "coordinator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only coordinators can reject event requests",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    reason_text = (action.reason if action and action.reason else "Event request rejected by coordinator")

    apply_status_change(db, event, REJECTED, current_user, reason=reason_text)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "rejection_reason": reason_text,
    }


@router.get("")
def list_my_events(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.lower() == "organiser":
        events = db.query(Event).filter(Event.organiser_id == current_user.id).all()
    elif current_user.role.lower() == "coordinator":
        events = db.query(Event).filter(Event.assigned_coordinator_id == current_user.id).all()
    else:
        events = db.query(Event).all()

    return [
        {
            "id": ev.id,
            "title": ev.title,
            "purpose": ev.purpose,
            "description": ev.description,
            "proposed_start": ev.proposed_start,
            "expected_attendance": ev.expected_attendance,
            "status": ev.status,
            "organiser_id": ev.organiser_id,
            "assigned_coordinator_id": ev.assigned_coordinator_id,
            "created_at": ev.created_at,
            "submitted_at": ev.submitted_at,
        }
        for ev in events
    ]
