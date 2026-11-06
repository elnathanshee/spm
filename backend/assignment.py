from datetime import datetime
from sqlalchemy.orm import Session

from .models import (
    CoordinatorAssignmentHistory,
    CoordinatorReassignment,
    Event,
    Notification,
    User,
)
from .notifications import (
    KIND_COORDINATOR_CHANGED,
    KIND_REASSIGNED_TO_YOU,
    can_see_event,
    notify,
    notify_first_assignment,
    notify_former_coordinator,
)


class AssignmentError(Exception):
    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


def require_lead(user: User) -> None:
    if (user.role or "").strip().lower() != "lead":
        raise AssignmentError("Only Event Coordinator Leads can assign event requests", 403)


def is_serving_coordinator(user: User) -> bool:
    return (user.role or "").strip().lower() == "coordinator" and bool(user.is_active_coordinator)


def get_active_coordinator(db: Session, coordinator_id: int) -> User:
    coordinator = db.get(User, coordinator_id)
    if coordinator is None or not is_serving_coordinator(coordinator):
        raise AssignmentError("Selected user is not an active Coordinator")
    return coordinator


def assign_event(db: Session, event: Event, coordinator: User, lead: User, *, reassign: bool) -> None:
    require_lead(lead)
    previous = event.assigned_coordinator
    now = datetime.utcnow()

    if reassign:
        if previous is None:
            raise AssignmentError("Event request is unassigned; use initial assignment")
        if previous.id == coordinator.id:
            raise AssignmentError("Event request is already assigned to this Coordinator")
    elif previous is not None:
        raise AssignmentError("Event request is already assigned; use reassignment")

    # [SCRUM-85] Someone who changed role can't receive this notice, but their event must still move.
    if reassign and previous is not None and can_see_event(previous, event):
        notify_former_coordinator(
            db,
            previous,
            event,
            f'Event "{event.title}" has been reassigned to another coordinator. You no longer manage it.',
        )

    event.assigned_coordinator_id = coordinator.id

    if reassign and previous is not None:
        db.add(CoordinatorReassignment(
            event_id=event.id,
            from_coordinator_id=previous.id,
            to_coordinator_id=coordinator.id,
            reassigned_by_id=lead.id,
            reassigned_at=now,
        ))

    db.add(CoordinatorAssignmentHistory(
        event_id=event.id,
        coordinator_id=coordinator.id,
        assigned_at=now,
    ))

    db.flush()

    if reassign:
        notify(
            db,
            coordinator,
            event,
            KIND_REASSIGNED_TO_YOU,
            f'Event "{event.title}" has been reassigned to you.',
        )
        organiser = event.organiser or db.get(User, event.organiser_id)
        if organiser:
            notify(
                db,
                organiser,
                event,
                KIND_COORDINATOR_CHANGED,
                f'Your event request "{event.title}" has a new coordinator: {coordinator.username}.',
            )
    else:
        notify_first_assignment(db, event)

    db.commit()
    db.refresh(event)

