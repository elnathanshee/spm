from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import CoordinatorAssignmentHistory, Event, User


class NoCoordinatorAvailable(Exception):
    pass


def get_round_robin_coordinator(db: Session) -> User:
    coordinators = (
        db.query(User)
        .filter(
            User.role.ilike("coordinator"),
            User.is_active_coordinator == True,
        )
        .order_by(User.id.asc())
        .all()
    )

    if not coordinators:
        raise NoCoordinatorAvailable("No active coordinator available")

    coordinator_latest_times = []
    for coord in coordinators:
        latest = (
            db.query(func.max(CoordinatorAssignmentHistory.assigned_at))
            .filter(CoordinatorAssignmentHistory.coordinator_id == coord.id)
            .scalar()
        )
        coordinator_latest_times.append((coord, latest))

    # Coordinators with no prior assignments come first, then oldest assignment timestamp
    coordinator_latest_times.sort(
        key=lambda item: (
            item[1] is not None,
            item[1] if item[1] is not None else datetime.min,
            item[0].id,
        )
    )

    return coordinator_latest_times[0][0]


def assign_coordinator_on_submission(event: Event, db: Session) -> None:
    if event.assigned_coordinator_id is not None:
        return

    coordinator = get_round_robin_coordinator(db)
    now = datetime.utcnow()
    event.assigned_coordinator_id = coordinator.id
    event.submitted_at = now

    history = CoordinatorAssignmentHistory(
        event_id=event.id,
        coordinator_id=coordinator.id,
        assigned_at=now,
    )
    db.add(history)
    db.commit()
    db.refresh(event)
