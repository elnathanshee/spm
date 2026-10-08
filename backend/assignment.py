from sqlalchemy.orm import Session

from .models import CoordinatorAssignmentHistory, Event, Notification, User


class AssignmentError(Exception):
    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


def require_lead(user: User) -> None:
    if (user.role or "").strip().lower() != "lead":
        raise AssignmentError("Only Event Coordinator Leads can assign event requests", 403)


def get_active_coordinator(db: Session, coordinator_id: int) -> User:
    coordinator = db.get(User, coordinator_id)
    if (
        coordinator is None
        or (coordinator.role or "").strip().lower() != "coordinator"
        or not coordinator.is_active_coordinator
    ):
        raise AssignmentError("Selected user is not an active Coordinator")
    return coordinator


def assign_event(db: Session, event: Event, coordinator: User, lead: User, *, reassign: bool) -> None:
    require_lead(lead)
    previous = event.assigned_coordinator

    if reassign:
        if previous is None:
            raise AssignmentError("Event request is unassigned; use initial assignment")
        if previous.id == coordinator.id:
            raise AssignmentError("Event request is already assigned to this Coordinator")
    elif previous is not None:
        raise AssignmentError("Event request is already assigned; use reassignment")

    event.assigned_coordinator_id = coordinator.id
    db.add(CoordinatorAssignmentHistory(event_id=event.id, coordinator_id=coordinator.id))

    if previous is not None:
        db.add(Notification(
            recipient_id=previous.id,
            event_id=event.id,
            message=f"Event request #{event.id} ({event.title}) was reassigned from you to {coordinator.username}.",
        ))

    action = "reassigned to you" if previous is not None else "assigned to you"
    db.add(Notification(
        recipient_id=coordinator.id,
        event_id=event.id,
        message=f"Event request #{event.id} ({event.title}) was {action} by {lead.username}.",
    ))
    db.commit()
    db.refresh(event)
