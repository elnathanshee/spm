"""SCRUM-23: Event Coordinator Lead reassigns an event to a different Coordinator.

Rewritten per Week 7 Customer Change #5. The 10 Sep handshake (Coordinators
agree offline, the new one accepts) is superseded: the Lead now reassigns.

Acceptance criteria covered:
  Lead role
    AC1  There is an Event Coordinator Lead role, with a seeded Lead account
         (``lead1`` in seed_users.py).
    AC2  The Lead can view all events and each event's current Coordinator
         (GET /events already returns every event for this role).
  Reassignment
    AC3  Only the Lead can reassign. Any other role is refused, including by
         a direct API call.
    AC4  After reassignment the event has exactly one assigned Coordinator.
    AC5  The previous Coordinator can no longer manage the event; the new one
         can. Nothing extra is needed for this: status_rules.py already only
         lets the *assigned* Coordinator act, so moving the assignment moves
         the permission.
    AC6  Each reassignment is recorded (by whom, from, to, when) in
         CoordinatorReassignment.
    AC7  The Organiser sees the current Coordinator (GET /events).
  Notification
    AC8  The previous Coordinator, the new Coordinator and the Organiser are
         notified (via the SCRUM-51 notification functions).
  Out of scope
    The unassigned queue (the Lead assigning new requests instead of
    round-robin) is a separate Change #5 story for Sprint 3.

Team assumptions (to be confirmed with the PO):
  * A request cannot be reassigned while it is a Draft (it has no Coordinator
    yet) or once it is finished (Rejected, Cancelled, Completed).
  * The new Coordinator must be an active Coordinator, and different from the
    current one.
  * The former Coordinator's notification stays in their list but is not
    linked to the event, since they can no longer open it.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import CoordinatorAssignmentHistory, CoordinatorReassignment, Event, User
from .notifications import (
    KIND_COORDINATOR_CHANGED,
    KIND_REASSIGNED_TO_YOU,
    notify,
    notify_former_coordinator,
)
from .status import CANCELLED, COMPLETED, DRAFT, REJECTED

router = APIRouter(prefix="/events", tags=["reassignment"])

LEAD = "lead"

# Statuses in which an event cannot be reassigned (team assumption).
NOT_REASSIGNABLE = {DRAFT, REJECTED, CANCELLED, COMPLETED}


class ReassignRequest(BaseModel):
    coordinator_id: int


def _role_of(user: User) -> str:
    return (user.role or "").strip().lower()


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


@router.post("/{event_id}/reassign")
def reassign_event(
    event_id: int,
    body: ReassignRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # AC3: only the Lead. Checked before anything else, so other roles learn
    # nothing about the event.
    if _role_of(current_user) != LEAD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Event Coordinator Lead can reassign events",
        )

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.status in NOT_REASSIGNABLE or event.assigned_coordinator_id is None:
        raise _bad_request(
            f"A request with status '{event.status}' cannot be reassigned"
        )

    new_coordinator = db.get(User, body.coordinator_id)
    if (
        new_coordinator is None
        or _role_of(new_coordinator) != "coordinator"
        or not new_coordinator.is_active_coordinator
    ):
        raise _bad_request("The new coordinator must be an active coordinator")

    if new_coordinator.id == event.assigned_coordinator_id:
        raise _bad_request("This event is already assigned to that coordinator")

    previous_coordinator = db.get(User, event.assigned_coordinator_id)
    organiser = db.get(User, event.organiser_id)
    now = datetime.utcnow()

    # AC8 (former Coordinator): must be sent while they are still assigned.
    notify_former_coordinator(
        db,
        previous_coordinator,
        event,
        f'Event "{event.title}" has been reassigned to another coordinator. '
        f"You no longer manage it.",
    )

    # AC4: exactly one Coordinator - the field is replaced, never added to.
    event.assigned_coordinator_id = new_coordinator.id

    # AC6: who, from, to, when.
    db.add(
        CoordinatorReassignment(
            event_id=event.id,
            from_coordinator_id=previous_coordinator.id,
            to_coordinator_id=new_coordinator.id,
            reassigned_by_id=current_user.id,
            reassigned_at=now,
        )
    )
    # Keep the existing assignment history complete, which round-robin
    # (SCRUM-22) also uses to spread the workload fairly.
    db.add(
        CoordinatorAssignmentHistory(
            event_id=event.id,
            coordinator_id=new_coordinator.id,
            assigned_at=now,
        )
    )

    # AC8 (new Coordinator and Organiser): sent after the change, so the
    # entitlement check in notify() sees the new assignment.
    db.flush()
    notify(
        db,
        new_coordinator,
        event,
        KIND_REASSIGNED_TO_YOU,
        f'Event "{event.title}" has been reassigned to you.',
    )
    notify(
        db,
        organiser,
        event,
        KIND_COORDINATOR_CHANGED,
        f'Your event request "{event.title}" has a new coordinator: '
        f"{new_coordinator.username}.",
    )

    # Everything above is saved together or not at all.
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "status": event.status,
        "assigned_coordinator_id": event.assigned_coordinator_id,
        "previous_coordinator_id": previous_coordinator.id,
        "reassigned_by": current_user.username,
    }
