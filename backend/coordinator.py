"""SCRUM-24: the Coordinator's list of event requests assigned to them.

Acceptance criteria covered:
  AC1  A logged-in Coordinator sees the event requests assigned to them.
  AC2  Each entry shows the event title, the Organiser, the proposed date and
       time, the expected attendance and the current status.
  AC3  Requests awaiting first review are distinguishable from those already
       being worked on.
  AC4  When nothing is assigned, the page says so instead of showing a blank
       list.
  AC5  A Coordinator sees only their own assigned requests.

The page is rendered by the backend rather than by a separate frontend app,
because the frontend framework is still undecided. The same rows are also
available as JSON at /coordinator/events/data, so whatever the team chooses
later can read them without this file changing.

"Awaiting first review" means the request is still Submitted: nobody has
started reviewing it. Once the assigned Coordinator starts a review
(SCRUM-27), it moves to Under Review and stops being counted as waiting.
"""

from datetime import datetime
from html import escape
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Event, User
from .status import DRAFT, SUBMITTED

router = APIRouter(prefix="/coordinator", tags=["coordinator"])

# A request nobody has picked up yet.
AWAITING_FIRST_REVIEW = SUBMITTED

_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def require_coordinator(current_user: User) -> User:
    """Only a Coordinator has an assigned-requests list."""
    if (current_user.role or "").strip().lower() != "coordinator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only coordinators can view assigned event requests",
        )
    return current_user


def format_datetime(value: Optional[datetime]) -> str:
    """A date a person can read at a glance, e.g. 14 Oct 2026, 2:30 pm."""
    if value is None:
        return "Not set"
    hour = value.hour % 12 or 12
    meridiem = "am" if value.hour < 12 else "pm"
    return (
        f"{value.day} {_MONTHS[value.month - 1]} {value.year}, "
        f"{hour}:{value.minute:02d} {meridiem}"
    )


def assigned_requests(db: Session, coordinator: User) -> List[Dict]:
    """The requests assigned to this Coordinator, most in need of attention first.

    Requests awaiting first review come first, longest wait at the top, so the
    list doubles as a work queue. Everything else follows in date order.
    """
    rows = (
        db.query(Event, User.username)
        .join(User, Event.organiser_id == User.id)
        .filter(Event.assigned_coordinator_id == coordinator.id)
        .all()
    )

    requests = []
    for event, organiser_name in rows:
        requests.append(
            {
                "id": event.id,
                "title": event.title,
                # The user record holds a username; there is no separate
                # display name field yet. If the team adds one, this is the
                # only line that needs to change.
                "organiser": organiser_name,
                "proposed_start": event.proposed_start,
                "proposed_start_display": format_datetime(event.proposed_start),
                "expected_attendance": event.expected_attendance,
                "status": event.status,
                "awaiting_first_review": event.status == AWAITING_FIRST_REVIEW,
                "submitted_at": event.submitted_at,
            }
        )

    far_future = datetime.max

    def queue_order(request):
        if request["awaiting_first_review"]:
            waiting_since = request["submitted_at"] or far_future
            return (0, waiting_since, request["id"])
        return (1, request["proposed_start"] or far_future, request["id"])

    requests.sort(key=queue_order)
    return requests


@router.get("/events/data")
def assigned_requests_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The same rows as the page, for a future frontend to read."""
    require_coordinator(current_user)
    requests = assigned_requests(db, current_user)
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "organiser": r["organiser"],
            "proposed_start": r["proposed_start"],
            "expected_attendance": r["expected_attendance"],
            "status": r["status"],
            "awaiting_first_review": r["awaiting_first_review"],
        }
        for r in requests
    ]


@router.get("/events/all-data")
def all_non_draft_events_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read-only visibility of every submitted or subsequent event."""
    require_coordinator(current_user)
    events = (
        db.query(Event)
        .filter(Event.status != DRAFT)
        .order_by(Event.submitted_at.asc(), Event.id.asc())
        .all()
    )
    return [
        {
            "id": event.id,
            "title": event.title,
            "organiser": event.organiser.username,
            "proposed_start": event.proposed_start,
            "expected_attendance": event.expected_attendance,
            "status": event.status,
            "assigned_coordinator": (
                event.assigned_coordinator.username if event.assigned_coordinator else None
            ),
            "is_assigned_to_me": event.assigned_coordinator_id == current_user.id,
        }
        for event in events
    ]


def _attendance_display(value: Optional[int]) -> str:
    if value is None:
        return "Not set"
    return f"{value} expected"


def _render_request(request: Dict) -> str:
    """One row of the queue. Every value is escaped, since titles are typed
    by Organisers and must never be treated as markup."""
    waiting = request["awaiting_first_review"]
    row_class = "request waiting" if waiting else "request"
    marker = (
        '<span class="marker waiting-marker">Awaiting your first review</span>'
        if waiting
        else f'<span class="marker">{escape(request["status"])}</span>'
    )
    return f"""
      <li class="{row_class}">
        <div class="headline">
          <h2>{escape(request["title"])}</h2>
          {marker}
        </div>
        <dl class="facts">
          <div><dt>Organiser</dt><dd>{escape(request["organiser"])}</dd></div>
          <div><dt>Proposed</dt><dd>{escape(request["proposed_start_display"])}</dd></div>
          <div><dt>Attendance</dt><dd>{escape(_attendance_display(request["expected_attendance"]))}</dd></div>
        </dl>
      </li>"""


def _render_summary(total: int, waiting: int) -> str:
    if waiting:
        return f"{total} assigned to you, {waiting} waiting for your first review"
    return f"{total} assigned to you, none waiting for a first review"


def render_page(coordinator: User, requests: List[Dict]) -> str:
    total = len(requests)
    waiting = sum(1 for r in requests if r["awaiting_first_review"])

    if requests:
        body = f"""
      <p class="summary">{escape(_render_summary(total, waiting))}</p>
      <ul class="queue">{"".join(_render_request(r) for r in requests)}
      </ul>"""
    else:
        body = """
      <div class="empty">
        <p>Nothing is assigned to you yet.</p>
        <p>New event requests appear here as soon as an Organiser submits one.</p>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assigned requests — ConnectSphere</title>
<style>
  :root {{
    --ink: #16181d;
    --muted: #5d636e;
    --hairline: #e2e4e8;
    --page: #ffffff;
    --waiting: #9a4a09;
    --waiting-wash: #fdf4ec;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem 1.25rem 4rem;
    background: var(--page);
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  main {{ max-width: 46rem; margin: 0 auto; }}
  header {{ border-bottom: 1px solid var(--hairline); padding-bottom: 1rem; }}
  h1 {{ font-size: 1.5rem; font-weight: 600; margin: 0 0 .25rem; letter-spacing: -0.01em; }}
  .who {{ margin: 0; color: var(--muted); font-size: .9rem; }}
  .summary {{ margin: 1.25rem 0 .5rem; color: var(--muted); font-size: .9rem; }}
  .queue {{ list-style: none; margin: 0; padding: 0; }}
  .request {{ padding: 1.1rem 0 1.1rem 1rem; border-bottom: 1px solid var(--hairline); border-left: 3px solid transparent; }}
  .request.waiting {{ border-left-color: var(--waiting); background: var(--waiting-wash); }}
  .headline {{ display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline; justify-content: space-between; }}
  h2 {{ font-size: 1.05rem; font-weight: 600; margin: 0; }}
  .marker {{ font-size: .8rem; color: var(--muted); white-space: nowrap; }}
  .waiting-marker {{ color: var(--waiting); font-weight: 600; }}
  .facts {{ display: flex; flex-wrap: wrap; gap: .35rem 2rem; margin: .5rem 0 0; }}
  .facts div {{ display: flex; gap: .4rem; }}
  dt {{ color: var(--muted); font-size: .875rem; }}
  dd {{ margin: 0; font-size: .875rem; font-variant-numeric: tabular-nums; }}
  .empty {{ padding: 3rem 0; color: var(--muted); }}
  .empty p {{ margin: 0 0 .4rem; }}
  .empty p:first-child {{ color: var(--ink); font-size: 1.05rem; }}
  a:focus-visible, li:focus-visible {{ outline: 2px solid var(--ink); outline-offset: 2px; }}
  @media (max-width: 34rem) {{
    .facts {{ flex-direction: column; gap: .2rem; }}
    .headline {{ flex-direction: column; }}
  }}
</style>
</head>
<body>
  <main>
    <header>
      <h1>Assigned requests</h1>
      <p class="who">Signed in as {escape(coordinator.username)}</p>
    </header>{body}
  </main>
</body>
</html>"""


@router.get("/events", response_class=HTMLResponse)
def assigned_requests_page(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Where a Coordinator lands after logging in."""
    require_coordinator(current_user)
    requests = assigned_requests(db, current_user)
    return HTMLResponse(content=render_page(current_user, requests))
