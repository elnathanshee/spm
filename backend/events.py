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
# [SCRUM-26 | Renee] Use Melvin's rulebook (SCRUM-27) to check every status change
from .status_rules import StatusChangeError, change_status
# [SCRUM-51 | Renee] Tell the Organiser when their request is submitted, returned, approved or rejected
from .notifications import notify_first_assignment, notify_organiser_of_status

router = APIRouter(prefix="/events", tags=["events"])


class ReviewAction(BaseModel):
    reason: Optional[str] = None


# [SCRUM-26 | Renee] NEW: request body for /reject. Reason is required and cannot be blank
# (missing/blank reason -> 422). Approve still uses the optional ReviewAction above.
class RejectAction(BaseModel):
    """SCRUM-26: a rejection must always say why."""

    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("A rejection reason is required")
        return v.strip()


# [SCRUM-25 | Renee] NEW: request body for /return. The clarification note is required
# and cannot be blank (missing/blank note -> 422), so the Organiser always knows what to fix.
class ReturnAction(BaseModel):
    """SCRUM-25: returning a request must say what is unclear or missing."""

    reason: str

    @field_validator("reason")
    @classmethod
    def note_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("A clarification note is required")
        return v.strip()


# [SCRUM-25 | Renee] NEW helper: the latest clarification note for a Returned request,
# read from EventStatusHistory. Used by GET /events so the Organiser can see what was asked.
def latest_clarification_note(event: Event) -> Optional[str]:
    if event.status != RETURNED:
        return None
    returns = [h for h in event.status_history if h.new_status == RETURNED]
    if not returns:
        return None
    return max(returns, key=lambda h: h.id).reason


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


# [SCRUM-21 | Elnathan] Request body for /amend
class EventAmend(BaseModel):
    title: Optional[str] = None
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
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (not v or not v.strip()):
            raise ValueError("Title cannot be empty")
        return v.strip() if v is not None else None



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

    # Allow submitting newly created drafts or resubmitting returned requests
    if event.status not in [DRAFT, RETURNED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit an event request with status '{event.status}'",
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

    prev_status = event.status
    event.status = SUBMITTED

    # Only assign a coordinator if one is not already assigned
    # [SCRUM-63 | Renee] remember whether this submission made the first assignment
    newly_assigned = False
    if not event.assigned_coordinator_id:
        try:
            assign_coordinator_on_submission(event, db)
            newly_assigned = True
        except NoCoordinatorAvailable as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=prev_status,
        new_status=SUBMITTED,
        actor_id=current_user.id,
        reason="Event request submitted by organiser",
    )
    db.add(status_history)
    # [SCRUM-51 | Renee] NEW: notify the Organiser (AC1). Saved in the same commit as the submission.
    notify_organiser_of_status(db, event, SUBMITTED)
    # [SCRUM-63 | Renee] NEW: first assignment notifies the Organiser (AC1) and the
    # Coordinator (AC3). A resubmission keeps the same Coordinator, so nothing is sent.
    if newly_assigned:
        notify_first_assignment(db, event)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "submitted_at": event.submitted_at,
    }


# [SCRUM-21 | Elnathan] Organiser amends own event request
@router.post("/{event_id}/amend")
def amend_event(
    event_id: int,
    payload: EventAmend,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SCRUM-21: Organiser amends own event request while Submitted, Under Review, or Returned."""
    if current_user.role.lower() != "organiser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organisers can amend event requests",
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
            detail="You are not authorized to amend this event request",
        )

    if event.status == DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Draft requests cannot be amended. Please edit the draft and submit.",
        )
    if event.status == APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot amend an approved event request. Please raise a change request.",
        )
    if event.status == REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot amend a rejected event request. Please raise a change request.",
        )
    if event.status not in [SUBMITTED, UNDER_REVIEW, RETURNED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot amend an event request in {event.status} status.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required to amend the request",
        )

    validate_event_business_rules(
        payload.proposed_start,
        payload.expected_attendance,
    )

    changed_fields = []
    for field, val in update_data.items():
        if hasattr(event, field):
            setattr(event, field, val)
            changed_fields.append(field)

    # Record status history row: previous_status == new_status == current_status
    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=event.status,
        new_status=event.status,
        actor_id=current_user.id,
        reason=f"Amended: {', '.join(changed_fields)}",
    )
    db.add(status_history)
    db.commit()
    db.refresh(event)

    return {
        "message": "Event amended successfully",
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
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "changed_fields": changed_fields,
    }


@router.post("/{event_id}/return")
def return_event_for_clarification(
    event_id: int,
    action: ReturnAction,
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

    # [SCRUM-25 | Renee] CHANGED: status guard + required note.
    # Before: set event.status = RETURNED directly and used a default note if none given.
    # Now: note is required (ReturnAction, else 422), and change_status() checks the rulebook -
    #   - only from Submitted / Under Review, otherwise 400
    #   - only the assigned coordinator, otherwise 403
    # The note is saved to EventStatusHistory.
    try:
        change_status(db, event, RETURNED, current_user, reason=action.reason)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    # [SCRUM-51 | Renee] NEW: notify the Organiser, including the Coordinator's note (AC2).
    # Only reached when the return was allowed, so a refused return sends nothing.
    notify_organiser_of_status(db, event, RETURNED, note=action.reason)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "clarification_reason": action.reason,
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

    Originally written by Melvin in the root-level events.py, which the merge
    left outside the backend package so the route was never served. Ported here
    against the rulebook: the original called its own apply_status_change
    helper, this calls change_status, so the Submitted -> Under Review guard,
    the assigned-coordinator check and the history row all come from one place.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    reason_text = (action.reason if action and action.reason else "Review started by coordinator")

    try:
        change_status(db, event, UNDER_REVIEW, current_user, reason=reason_text)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

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

    # [SCRUM-26 | Renee] CHANGED: status guard.
    # Before: set event.status = APPROVED directly (any status, any coordinator).
    # Now: change_status() checks the rulebook first -
    #   - only from Submitted / Under Review, otherwise 400
    #   - only the assigned coordinator, otherwise 403
    # and it writes the EventStatusHistory row for us.
    try:
        change_status(db, event, APPROVED, current_user, reason=reason_text)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    # [SCRUM-51 | Renee] NEW: notify the Organiser that it was approved (AC3).
    notify_organiser_of_status(db, event, APPROVED)
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
    action: RejectAction,
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

    # [SCRUM-26 | Renee] CHANGED: status guard + required reason.
    # Before: set event.status = REJECTED directly and used a default reason if none given.
    # Now: reason is required (RejectAction, else 422), and change_status() checks the rulebook -
    #   - only from Submitted / Under Review, otherwise 400
    #   - only the assigned coordinator, otherwise 403
    # The reason is saved to EventStatusHistory.
    try:
        change_status(db, event, REJECTED, current_user, reason=action.reason)
    except StatusChangeError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    # [SCRUM-51 | Renee] NEW: notify the Organiser that it was rejected, with the reason (AC3).
    notify_organiser_of_status(db, event, REJECTED, note=action.reason)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "rejection_reason": action.reason,
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
            # [SCRUM-25 | Renee] NEW: the Coordinator's note when the request is Returned, else None
            "clarification_note": latest_clarification_note(ev),
        }
        for ev in events
    ]
