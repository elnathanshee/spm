from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .assignment import NoCoordinatorAvailable, assign_coordinator_on_submission
from .auth import get_current_user
from .database import get_db
from .models import Event, EventStatusHistory, User
from .status import APPROVED, DRAFT, REJECTED, RETURNED, SUBMITTED

router = APIRouter(prefix="/events", tags=["events"])


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
    if not event.assigned_coordinator_id:
        try:
            assign_coordinator_on_submission(event, db)
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

    prev_status = event.status
    event.status = RETURNED
    reason_text = (action.reason if action and action.reason else "Returned to organiser for clarification")

    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=prev_status,
        new_status=RETURNED,
        actor_id=current_user.id,
        reason=reason_text,
    )
    db.add(status_history)
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "clarification_reason": reason_text,
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

    prev_status = event.status
    event.status = APPROVED
    reason_text = (action.reason if action and action.reason else "Event request approved by coordinator")

    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=prev_status,
        new_status=APPROVED,
        actor_id=current_user.id,
        reason=reason_text,
    )
    db.add(status_history)
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

    prev_status = event.status
    event.status = REJECTED
    reason_text = (action.reason if action and action.reason else "Event request rejected by coordinator")

    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=prev_status,
        new_status=REJECTED,
        actor_id=current_user.id,
        reason=reason_text,
    )
    db.add(status_history)
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


# ---------------------------------------------------------------------------
# SCRUM-21: Organiser amends their own request while it is awaiting review
# ---------------------------------------------------------------------------
# UNDER_REVIEW is imported on its own line because the import at the top of this
# file does not list it, and that existing line must not be modified. Note that
# the name "status" is already bound to fastapi.status above, so writing
# "status.UNDER_REVIEW" here would not work.
from .status import UNDER_REVIEW


class EventAmend(BaseModel):
    """Fields an Organiser is allowed to correct on a request awaiting review.

    Workflow columns (status, organiser_id, assigned_coordinator_id,
    submitted_at) are deliberately absent. Pydantic drops keys it does not
    declare, so a body such as {"status": "Approved"} is discarded before it can
    reach the event.
    """

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
        # A title that was not supplied at all is fine; a supplied blank one is not.
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Title cannot be empty")
        return v.strip()


@router.post("/{event_id}/amend")
def amend_event(
    event_id: int,
    amendment: EventAmend,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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

    # AC4: an Organiser may only amend a request they own.
    if event.organiser_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to amend this event request",
        )

    # AC3: once a decision has been made the request is final, and the Organiser
    # is told what to do instead. This is checked before anything is written, so
    # the existing request is left unchanged.
    if event.status in [APPROVED, REJECTED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This request has already been {event.status.lower()} and can no "
                f"longer be amended. Please raise a change request instead."
            ),
        )

    # AC1: amendment is for requests still awaiting review, which includes
    # Returned. Anything else is refused, so a status that a later sprint adds
    # cannot be amended by default.
    if event.status not in [SUBMITTED, UNDER_REVIEW, RETURNED]:
        # The message names the actual status so the Organiser can see why they
        # were refused, not just that they were.
        refusal = (
            f"This request has status '{event.status}' and cannot be amended. "
            f"Only a request awaiting review (Submitted, Under Review or "
            f"Returned) can be amended."
        )
        # A Draft has not been submitted yet, so the way forward is to finish and
        # submit it. Telling the Organiser to raise a change request would be
        # wrong advice here, so that wording is deliberately not used.
        if event.status == DRAFT:
            refusal = refusal + " A Draft should be completed and submitted instead."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=refusal,
        )

    # exclude_unset keeps only the fields the Organiser actually sent, so an
    # omitted field is left alone rather than being overwritten with None.
    changes = amendment.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields supplied to amend. Send at least one field to change.",
        )

    # Only the values actually supplied are validated. Re-checking the stored
    # proposed_start would wrongly refuse an unrelated amendment to a request
    # whose start date has since passed.
    validate_event_business_rules(
        changes.get("proposed_start"),
        changes.get("expected_attendance"),
    )

    changed_fields = []
    for field_name, new_value in changes.items():
        if getattr(event, field_name) != new_value:
            setattr(event, field_name, new_value)
            changed_fields.append(field_name)

    if changed_fields:
        reason_text = "Request amended by organiser. Fields changed: " + ", ".join(changed_fields)
    else:
        reason_text = "Request amended by organiser. No field values changed."

    # AC2: the status is carried across unchanged. Recording the same value as
    # both previous and new status is the audit evidence that amending a request
    # does not send it back to Draft.
    status_history = EventStatusHistory(
        event_id=event.id,
        previous_status=event.status,
        new_status=event.status,
        actor_id=current_user.id,
        reason=reason_text,
    )
    db.add(status_history)
    db.commit()
    db.refresh(event)

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
        "amended_fields": changed_fields,
    }
