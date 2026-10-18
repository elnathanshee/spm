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
  * Sprint 1 ends at "Approved" or "Rejected". "Planning", "Confirmed",
    "Completed" and "Cancelled" exist as vocabulary but nothing moves into them
    yet; those transitions arrive with their own stories in later sprints.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from .models import Event, EventStatusHistory, User
from .status import (
    APPROVED,
    DRAFT,
    REJECTED,
    RETURNED,
    SUBMITTED,
    UNDER_REVIEW,
)

ORGANISER = "organiser"
COORDINATOR = "coordinator"


# The rulebook. Each entry is (from status, to status) -> the role allowed to
# make that move. A pair that is absent from this table is not a legal move.
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
    return (user.role or "").strip().lower()


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

    if _role_of(actor) != required_role:
        raise NotPermitted(
            f"Only a {required_role} can move a request from '{current}' to "
            f"'{new_status}'."
        )

    if required_role == ORGANISER and event.organiser_id != actor.id:
        raise NotPermitted(
            "You are not the organiser of this event request."
        )

    if required_role == COORDINATOR and event.assigned_coordinator_id != actor.id:
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
