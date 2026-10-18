from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Event, EventStatusHistory, User, VenueBooking
from .status import APPROVED, CONFIRMED, DRAFT, PLANNING, REJECTED, RETURNED, SUBMITTED, UNDER_REVIEW
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
    event.submitted_at = datetime.utcnow()

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
    amendment: EventAmend,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (current_user.role or "").strip().lower() != "organiser":
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
            detail="At least one field is required to amend the request",
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
        "organiser_id": event.organiser_id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "amended_fields": changed_fields,
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
    role = (current_user.role or "").strip().lower()
    if role == "organiser":
        events = db.query(Event).filter(Event.organiser_id == current_user.id).all()
    elif role == "coordinator":
        events = db.query(Event).filter(Event.assigned_coordinator_id == current_user.id).all()
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organisers and coordinators can view event requests",
        )

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


@router.get("/{event_id}")
def get_visible_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read one event when the caller's role and relationship permit it."""
    role = (current_user.role or "").strip().lower()
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if role == "organiser":
        # Deliberately conceal whether another organiser's event exists.
        if event.organiser_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    elif role in {"coordinator", "lead"}:
        if event.status == DRAFT:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    elif role == "attendee":
        if event.status != CONFIRMED:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        confirmed_booking = (
            db.query(VenueBooking)
            .filter(
                VenueBooking.event_id == event.id,
                VenueBooking.status == "Confirmed",
            )
            .order_by(VenueBooking.booking_start.asc(), VenueBooking.id.asc())
            .first()
        )
        return {
            "name": event.title,
            "description": event.description,
            "date": event.proposed_start.date().isoformat() if event.proposed_start else None,
            "time": event.proposed_start.time().isoformat() if event.proposed_start else None,
            "venue_name": (
                confirmed_booking.venue.name
                if confirmed_booking and confirmed_booking.venue
                else None
            ),
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this event request",
        )

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
        "status": event.status,
        "organiser_id": event.organiser_id,
        "assigned_coordinator_id": event.assigned_coordinator_id,
    }


@router.get("/{event_id}/bookings")
def get_event_bookings(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Internal booking data for related Coordinators, Leads and Venue Staff."""
    role = (current_user.role or "").strip().lower()
    if role not in {"coordinator", "lead", "venue_staff"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only assigned Coordinators, Leads and Venue Staff can view event bookings",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if role == "coordinator" and event.assigned_coordinator_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coordinators can view bookings only for events assigned to them",
        )

    bookings = (
        db.query(VenueBooking)
        .filter(VenueBooking.event_id == event_id)
        .order_by(VenueBooking.booking_start.asc())
        .all()
    )
    return [
        {
            "id": b.id,
            "booking_id": b.id,
            "event_id": b.event_id,
            "venue_id": b.venue_id,
            "venue_name": b.venue.name if b.venue else None,
            "booking_start": b.booking_start.isoformat(),
            "booking_end": b.booking_end.isoformat(),
            "status": b.status,
            "rejection_reason": (b.decision.reason if hasattr(b, "decision") and b.decision else None) or getattr(b, "rejection_reason", None),
            "created_at": b.created_at.isoformat() if hasattr(b, "created_at") and b.created_at else None,
        }
        for b in bookings
    ]


# ---------------------------------------------------------------------------
# [SCRUM-28 | Renee] View and update event information during planning
# ---------------------------------------------------------------------------

# "During planning" = after approval. Before approval the Organiser uses
# POST /events/{id}/amend (SCRUM-21).
PLANNING_STATUSES = (APPROVED, PLANNING, CONFIRMED)

# Routine fields can be edited directly (AC2). Significant fields affect
# arrangements already made and belong to SCRUM-29, so they are refused here.
ROUTINE_FIELDS = ("title", "purpose", "description", "accessibility_needs", "registration_needs")
SIGNIFICANT_FIELDS = ("proposed_start", "expected_attendance", "venue_requirements", "equipment_requirements")


class EventRoutineUpdate(BaseModel):
    """Body for PATCH /events/{id}. Unknown fields (e.g. status) are refused."""
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    purpose: Optional[str] = None
    description: Optional[str] = None
    accessibility_needs: Optional[str] = None
    registration_needs: Optional[str] = None
    # Accepted by the schema only so they can be refused with a clear message
    proposed_start: Optional[Any] = None
    expected_attendance: Optional[Any] = None
    venue_requirements: Optional[Any] = None
    equipment_requirements: Optional[Any] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Title cannot be empty")
        return v.strip() if v is not None else None


def event_to_dict(event: Event) -> Dict[str, Any]:
    """AC1: the full event record."""
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
        "status": event.status,
        "organiser_id": event.organiser_id,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "created_at": event.created_at,
        "submitted_at": event.submitted_at,
    }


def _get_entitled_event(event_id: int, user: User, db: Session) -> Event:
    """AC2/AC3: only the event's Organiser and its assigned Coordinator may
    update it. Viewing is GET /events/{event_id} (SCRUM-18 visibility rules)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    role = (user.role or "").strip().lower()
    entitled = (
        (role == "organiser" and event.organiser_id == user.id)
        or (role == "coordinator" and event.assigned_coordinator_id == user.id)
    )
    if not entitled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorised to view or update this event",
        )
    return event


@router.patch("/{event_id}")
def update_event_routine_info(
    event_id: int,
    payload: EventRoutineUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SCRUM-28 AC2, AC3: update routine information during planning.

    Significant fields (date/time, attendance, venue or equipment requirements)
    are refused with 422; they are handled by SCRUM-29. Every update is recorded
    in the event's history: who changed which fields.
    """
    event = _get_entitled_event(event_id, current_user, db)

    update_data = payload.model_dump(exclude_unset=True)
    significant = [f for f in SIGNIFICANT_FIELDS if f in update_data]
    if significant:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{', '.join(significant)} cannot be changed here: this is a significant change (SCRUM-29)",
        )

    if event.status not in PLANNING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Event information can only be updated during planning "
                f"({', '.join(PLANNING_STATUSES)}); this event is {event.status}. "
                f"Before approval, use POST /events/{{id}}/amend."
            ),
        )

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required to update the event",
        )

    changed_fields = []
    for field in ROUTINE_FIELDS:
        if field in update_data:
            setattr(event, field, update_data[field])
            changed_fields.append(field)

    db.add(EventStatusHistory(
        event_id=event.id,
        previous_status=event.status,
        new_status=event.status,
        actor_id=current_user.id,
        reason=f"Updated: {', '.join(changed_fields)}",
    ))
    db.commit()
    db.refresh(event)

    data = event_to_dict(event)
    data["message"] = "Event updated successfully"
    data["changed_fields"] = changed_fields
    return data
