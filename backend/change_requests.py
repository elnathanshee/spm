"""[SCRUM-49, SCRUM-50] Change requests: changing an event after submission.

The Organiser asks, with a reason (SCRUM-49). Nothing on the event changes until
the assigned Coordinator approves (SCRUM-50). The requested and previous values
are kept as JSON text in a new table, because create_all never alters old ones.
"""

import json
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .booking_rules import singapore_time, to_naive_utc
from .database import get_db
from .events import (
    SIGNIFICANT_FIELDS,
    EventAmend,
    _get_entitled_event,
    validate_event_business_rules,
)
from .models import Event, EventChangeRequest, User
from .status import APPROVED, CONFIRMED, PLANNING, SAFETY_REVIEW, SUBMITTED, UNDER_REVIEW

router = APIRouter(prefix="/events", tags=["change requests"])

PENDING = "Pending"
APPROVED_REQUEST = "Approved"
REJECTED_REQUEST = "Rejected"

# [SCRUM-49] The statuses named by the story. Before these the Organiser edits the
# Draft or the Returned request directly; after them the event is finished.
CHANGEABLE_STATUSES = (SUBMITTED, UNDER_REVIEW, APPROVED, PLANNING, SAFETY_REVIEW, CONFIRMED)

# [SCRUM-29] SIGNIFICANT_FIELDS in events.py stays the one list of important fields.
# Description is the one routine field a change request may carry.
PERMITTED_FIELDS = SIGNIFICANT_FIELDS + ("description",)

FIELD_LABELS = {
    "proposed_start": "date and time",
    "expected_attendance": "expected attendance",
    "venue_requirements": "venue requirements",
    "equipment_requirements": "equipment requirements",
    "description": "description",
}


class ChangeRequestCreate(BaseModel):
    changes: Dict[str, Any]
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("A reason is required")
        return v.strip()


def _names(fields: List[str]) -> str:
    """The fields as the user reads them, for messages."""
    return ", ".join(FIELD_LABELS[field] for field in fields)


def _parse_values(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Turn sent or stored values into column values, with amend's own field types."""
    try:
        return EventAmend.model_validate(changes).model_dump(exclude_unset=True)
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors()})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"These values can't be used: {_names(fields)}. Please check them and try again.",
        )


def _clean_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
    """[SCRUM-49] Refuse what isn't permitted, and tidy the rest into column values."""
    refused = sorted(field for field in changes if field not in PERMITTED_FIELDS)
    if refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A change request can't change: {', '.join(refused)}.",
        )
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please choose at least one field to change.",
        )

    clean = {}
    for field, value in _parse_values(changes).items():
        if isinstance(value, str):
            value = value.strip() or None  # Blank text means the same as an empty column.
        if isinstance(value, datetime) and value.tzinfo is not None:
            # events.proposed_start is naive Singapore wall time.
            value = singapore_time(to_naive_utc(value))
        if value is None and field in ("proposed_start", "expected_attendance"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Please give a new value for {FIELD_LABELS[field]}.",
            )
        clean[field] = value
    return clean


def _real_differences(event: Event, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Only the changes that differ from the event. An empty string counts as empty."""
    return {field: value for field, value in changes.items() if (getattr(event, field) or None) != value}


def _to_json(values: Dict[str, Any]) -> str:
    return json.dumps({
        field: value.isoformat() if isinstance(value, datetime) else value
        for field, value in values.items()
    })


def _change_request_to_dict(change_request: EventChangeRequest) -> Dict[str, Any]:
    decider = change_request.decided_by
    return {
        "id": change_request.id,
        "event_id": change_request.event_id,
        "requested_by": change_request.requested_by.username,
        "requested_by_id": change_request.requested_by_id,
        "changes": json.loads(change_request.requested_changes),
        "previous_values": json.loads(change_request.previous_values),
        "reason": change_request.reason,
        "status": change_request.status,
        "decided_by": decider.username if decider else None,
        "decided_by_id": change_request.decided_by_id,
        "decision_reason": change_request.decision_reason,
        "created_at": change_request.created_at,
        "decided_at": change_request.decided_at,
    }


def _has_pending_request(db: Session, event: Event) -> bool:
    return (
        db.query(EventChangeRequest.id)
        .filter(EventChangeRequest.event_id == event.id, EventChangeRequest.status == PENDING)
        .first()
        is not None
    )


PENDING_REFUSAL = (
    "This event already has a pending change request. "
    "Please wait for the Coordinator's decision before asking again."
)


@router.post("/{event_id}/change-requests", status_code=status.HTTP_201_CREATED)
def raise_change_request(
    event_id: int,
    payload: ChangeRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """[SCRUM-49] The owning Organiser asks for a permitted change, with a reason."""
    if (current_user.role or "").strip().lower() != "organiser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event's Organiser can raise a change request",
        )
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.organiser_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to raise a change request for this event",
        )
    if event.status not in CHANGEABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"A change request can't be raised for an event with status '{event.status}'. "
                f"It's for an event that is {', '.join(CHANGEABLE_STATUSES)}."
            ),
        )
    if event.assigned_coordinator_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This event has no Coordinator assigned yet, so nobody can decide a change request. "
                "Please try again once a Lead has assigned one."
            ),
        )
    if _has_pending_request(db, event):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PENDING_REFUSAL)

    clean = _clean_changes(payload.changes)
    validate_event_business_rules(clean.get("proposed_start"), clean.get("expected_attendance"))

    changes = _real_differences(event, clean)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The event already has these values, so there is nothing to change.",
        )

    change_request = EventChangeRequest(
        event_id=event.id,
        requested_by_id=current_user.id,
        requested_changes=_to_json(changes),
        previous_values=_to_json({field: getattr(event, field) for field in changes}),
        reason=payload.reason,
        status=PENDING,
    )
    db.add(change_request)
    try:
        db.flush()
    except IntegrityError:
        # Two requests raced past the check above: the unique index stopped the second.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PENDING_REFUSAL)
    db.commit()
    db.refresh(change_request)
    return _change_request_to_dict(change_request)


@router.get("/{event_id}/change-requests")
def list_change_requests(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """[SCRUM-49] The event's Organiser and assigned Coordinator read its requests, newest first."""
    event = _get_entitled_event(event_id, current_user, db)
    rows = (
        db.query(EventChangeRequest)
        .filter(EventChangeRequest.event_id == event.id)
        .order_by(EventChangeRequest.id.desc())
        .all()
    )
    return [_change_request_to_dict(row) for row in rows]
