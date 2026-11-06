"""Event status rules (SCRUM-27).

This module is the single place that decides whether an event may move from
one status to another, and who is allowed to make that move. Every endpoint
that changes an event's status calls ``change_status`` here instead of
assigning ``event.status`` itself.

Acceptance criteria covered:
  AC1  An event holds exactly one status at a time, drawn from the agreed
       vocabulary in ``status.py``.
  AC3  Every status change records which user made it and when. That record is
       written here so it cannot be forgotten by a caller.

Team decisions recorded here, so they can be explained later:

  * "Returned" is a real tenth status (Option A), not a reuse of "Draft".
  * "Under Review" is optional. A coordinator may start review first and then
    decide, or decide straight from "Submitted". Both are legal, which keeps
    the existing SCRUM-21, SCRUM-25 and SCRUM-26 flows working unchanged.
  * No status changes on its own. The customer confirmed this three times, so
    every move here is the result of a person performing an action.
  * [SCRUM-102] After approval: Approved -> Planning -> Safety Review ->
    Confirmed -> Completed. Safety Review -> Confirmed is the only route into
    Confirmed, so no event gets there without a Safety Officer's approval.
  * [SCRUM-102] Cancelled is reachable from every stage before Completed, and
    several roles may do it, so those entries hold a tuple of roles.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from .models import Event, EventStatusHistory, User
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

ORGANISER = "organiser"
COORDINATOR = "coordinator"
LEAD = "lead"  # [SCRUM-102]
SAFETY_OFFICER = "safety_officer"  # [SCRUM-86]


# The rulebook. Each entry is (from status, to status) -> the role allowed to
# make that move, or a tuple of roles when several may. A pair that is absent
# from this table is not a legal move.
#
# Organiser moves are additionally restricted to the organiser who owns the
# request; coordinator moves to the coordinator assigned to it. Those two extra
# checks are applied in ``check_transition`` below.
TRANSITIONS = {
    (DRAFT, SUBMITTED): ORGANISER,
    (RETURNED, SUBMITTED): ORGANISER,
    (SUBMITTED, UNDER_REVIEW): COORDINATOR,
    (SUBMITTED, APPROVED): COORDINATOR,
    (SUBMITTED, REJECTED): COORDINATOR,
    (SUBMITTED, RETURNED): COORDINATOR,
    (UNDER_REVIEW, APPROVED): COORDINATOR,
    (UNDER_REVIEW, REJECTED): COORDINATOR,
    (UNDER_REVIEW, RETURNED): COORDINATOR,
    # [SCRUM-102] Planning through to completion.
    (APPROVED, PLANNING): COORDINATOR,
    (PLANNING, SAFETY_REVIEW): COORDINATOR,
    (CONFIRMED, COMPLETED): COORDINATOR,
    # [SCRUM-86] Only the Safety Officer decides a safety check.
    (SAFETY_REVIEW, CONFIRMED): SAFETY_OFFICER,
    (SAFETY_REVIEW, PLANNING): SAFETY_OFFICER,
    # [SCRUM-102] Before approval the owning Organiser may cancel too. Rejected,
    # Completed and Cancelled are absent: those events can't be cancelled.
    (DRAFT, CANCELLED): (ORGANISER, COORDINATOR, LEAD),
    (SUBMITTED, CANCELLED): (ORGANISER, COORDINATOR, LEAD),
    (UNDER_REVIEW, CANCELLED): (ORGANISER, COORDINATOR, LEAD),
    (RETURNED, CANCELLED): (ORGANISER, COORDINATOR, LEAD),
    (APPROVED, CANCELLED): (COORDINATOR, LEAD),
    (PLANNING, CANCELLED): (COORDINATOR, LEAD),
    (SAFETY_REVIEW, CANCELLED): (COORDINATOR, LEAD),
    (CONFIRMED, CANCELLED): (COORDINATOR, LEAD),
}


class StatusChangeError(Exception):
    """A status change was refused.

    ``http_status`` lets an endpoint turn this into the right HTTP response
    without this module needing to know anything about FastAPI.
    """

    http_status = 400


class InvalidTransition(StatusChangeError):
    """The move itself is not allowed from the event's current status."""

    http_status = 400


class NotPermitted(StatusChangeError):
    """The move exists, but this user is not the one allowed to make it."""

    http_status = 403


def _role_of(user: User) -> str:
    # [SCRUM-86] A space and an underscore are the same role (auth.py takes both).
    return (user.role or "").strip().lower().replace(" ", "_")


def allowed_next_statuses(current_status: str) -> List[str]:
    """The statuses an event in ``current_status`` may legally move to."""
    return sorted({to for (frm, to) in TRANSITIONS if frm == current_status})


def check_transition(event: Event, new_status: str, actor: User) -> None:
    """Refuse the move, with a reason, or return quietly if it is allowed.

    Nothing is written here, so a refused move leaves the event untouched.
    """
    current = event.status

    if new_status == current:
        raise InvalidTransition(
            f"This request already has status '{current}'."
        )

    required_role = TRANSITIONS.get((current, new_status))
    if required_role is None:
        onward = allowed_next_statuses(current)
        options = ", ".join(onward) if onward else "no further changes"
        raise InvalidTransition(
            f"A request with status '{current}' cannot be moved to "
            f"'{new_status}'. Allowed from here: {options}."
        )

    # [SCRUM-102] One role, or a tuple of them.
    allowed_roles = required_role if isinstance(required_role, tuple) else (required_role,)
    role = _role_of(actor)
    if role not in allowed_roles:
        names = " or ".join(name.replace("_", " ") for name in allowed_roles)
        raise NotPermitted(
            f"Only a {names} can move a request from '{current}' to "
            f"'{new_status}'."
        )

    if role == ORGANISER and event.organiser_id != actor.id:
        raise NotPermitted(
            "You are not the organiser of this event request."
        )

    if role == COORDINATOR and event.assigned_coordinator_id != actor.id:
        raise NotPermitted(
            "Only the coordinator assigned to this event request can make "
            "this change."
        )


def change_status(
    db: Session,
    event: Event,
    new_status: str,
    actor: User,
    reason: Optional[str] = None,
) -> EventStatusHistory:
    """Apply a status change after checking it is allowed.

    Sets the new status and adds the history row recording who changed it and
    when. The caller commits, so the status change and whatever else that
    endpoint is doing are saved together or not at all.
    """
    check_transition(event, new_status, actor)

    previous = event.status
    event.status = new_status

    history = EventStatusHistory(
        event_id=event.id,
        previous_status=previous,
        new_status=new_status,
        actor_id=actor.id,
        reason=reason,
    )
    db.add(history)
    return history
