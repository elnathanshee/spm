"""SCRUM-38 (part 2): approval re-checks conflicts, so a venue is never
confirmed to two events for one period.

Acceptance criteria covered by part 2:
  AC2  A confirmed booking makes that venue unavailable to other events for the
       corresponding period.
  AC3  A Coordinator attempting to book a period already confirmed to another
       event is told it is unavailable rather than allowed to proceed.
  AC4  Allocation follows first come, first served; confirmed bookings are not
       manually overridden.

PO decisions tested:
  D5  Approving re-checks the booking against the venue's other blocking
      bookings (find_conflicts with exclude_booking_id), using the venue's
      current setup and turnaround times. On a clash: 409, the booking stays
      Pending and no decision is recorded.
  D6  No automatic winner: Venue Staff reject one of the clashing bookings
      with a reason. A clash never blocks a reject.
  D7  check_venue_availability gains exclude_booking_id; the approval message
      is the request-time message prefixed with "Cannot approve: ".

Main already met most of AC2-AC4 before part 2 (SCRUM-35, 36, 37 and part 1);
TC-3815 to TC-3819, TC-3821, TC-3823 and TC-3824 are SCRUM-38's acceptance
checks for those points and pass before and after the change. TC-3820, TC-3822
and TC-3825 cover what part 2 adds.

Since SCRUM-18 (PR 24) a Coordinator may request a venue only for an event
assigned to them, so every event here is created with its Coordinator assigned.
All dates are relative to today (D = today + 30 days).
"""

import os
import sys
import types
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import booking_rules
from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, User, Venue, VenueBooking, VenueBookingDecision
from backend.security import hash_password
from backend.venues import check_venue_availability

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"
D = date.today() + timedelta(days=30)
REASON = {"reason": "Clashes with another booking"}


def at(hour, minute=0):
    return datetime.combine(D, time(hour, minute))


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def clock(monkeypatch):
    """SCRUM-81's one clock, set by the test; starts at the real UTC now."""
    c = types.SimpleNamespace(now=datetime.now(timezone.utc).replace(tzinfo=None))
    monkeypatch.setattr(booking_rules, "utc_now", lambda: c.now)
    return c


@pytest.fixture
def users(db_session):
    """Token for each: tok_<username>."""
    created = {}
    for username, role, active in [
        ("org1", "organiser", False),
        ("coord_a", "coordinator", True),
        ("coord_b", "coordinator", True),
        ("venue1", "venue_staff", False),
    ]:
        user = User(username=username, password_hash=hash_password("password123"), role=role,
                    is_active_coordinator=active)
        db_session.add(user)
        db_session.commit()
        db_session.add(LoginSession(user_id=user.id, token=f"tok_{username}", valid=True))
        db_session.commit()
        created[username] = user
    return created


def add_event(db_session, users, title, coordinator):
    event = Event(title=title, expected_attendance=50, organiser_id=users["org1"].id, status="Approved",
                  assigned_coordinator_id=users[coordinator].id)
    db_session.add(event)
    db_session.commit()
    return event


@pytest.fixture
def e1(db_session, users):
    """Event E1, assigned to coord_a."""
    return add_event(db_session, users, "Annual Gala", "coord_a")


@pytest.fixture
def e2(db_session, users):
    """Event E2, assigned to coord_b."""
    return add_event(db_session, users, "Spring Fair", "coord_b")


def add_venue(db_session, name):
    venue = Venue(name=name, location="Block A", capacity=200, setup_time_mins=30, turnaround_time_mins=45)
    db_session.add(venue)
    db_session.commit()
    return venue


@pytest.fixture
def v(db_session, users):
    """Venue V: 30 min setup, 45 min turnaround."""
    return add_venue(db_session, "Grand Hall")


def login(client, username):
    client.cookies.set(COOKIE, f"tok_{username}")


def request(client, venue, event, start, end, coordinator):
    login(client, coordinator)
    return client.post(
        f"/venues/{venue.id}/bookings",
        json={"event_id": event.id, "booking_start": start.isoformat(), "booking_end": end.isoformat()},
    )


def hold(client, venue, event, start, end, coordinator):
    resp = request(client, venue, event, start, end, coordinator)
    assert resp.status_code == 201, resp.text
    return resp.json()["booking"]["booking_id"]


def approve(client, booking_id):
    login(client, "venue1")
    return client.post(f"/venues/bookings/{booking_id}/approve")


def reject(client, booking_id):
    login(client, "venue1")
    return client.post(f"/venues/bookings/{booking_id}/reject", json=REASON)


def confirmed(client, venue, event, start, end, coordinator):
    booking_id = hold(client, venue, event, start, end, coordinator)
    assert approve(client, booking_id).status_code == 200
    return booking_id


def view(client, booking_id):
    login(client, "venue1")
    return client.get(f"/venues/bookings/{booking_id}").json()


def set_turnaround(client, venue, minutes):
    login(client, "venue1")
    resp = client.patch(f"/venues/{venue.id}", json={"turnaround_time_mins": minutes})
    assert resp.status_code == 200, resp.text


# ================================================================ what main already meets (acceptance checks)


def test_tc3815_a_confirmed_booking_blocks_the_period(client, v, e1, e2):
    """TC-3815 | Happy Path | AC2
    coord_a requests V for E1, D 10:00-12:00, and Venue Staff approve it.
    Expected: Confirmed; a search for D 10:00-12:00 shows V unavailable; the
    calendar shows D busy; coord_b's request for E2, 11:00-13:00, gets 409.
    Also proven by TC-3613, TC-3503 and TC-3808."""
    booking_id = confirmed(client, v, e1, at(10), at(12), "coord_a")
    assert view(client, booking_id)["status"] == "Confirmed"

    login(client, "coord_a")
    params = {"start_time": at(10).isoformat(), "end_time": at(12).isoformat()}
    [row] = [x for x in client.get("/venues/search", params=params).json() if x["name"] == "Grand Hall"]
    assert row["is_available"] is False
    calendar = client.get(f"/venues/{v.id}/calendar", params={"from": D.isoformat(), "to": D.isoformat()}).json()
    assert [b["status"] for b in calendar["days"][0]["busy"]] == ["Confirmed"]
    assert request(client, v, e2, at(11), at(13), "coord_b").status_code == 409


def test_tc3816_a_coordinator_is_told_the_period_is_unavailable(client, db_session, v, e1, e2):
    """TC-3816 | Negative Path | AC3
    With a Confirmed booking D 10:00-12:00, coord_b requests 10:30-11:30 for E2.
    Expected: 409 "Direct conflict with existing booking (D 10:00 - 12:00)", and
    no booking is created. Also proven by TC-3503 and TC-3809."""
    confirmed(client, v, e1, at(10), at(12), "coord_a")

    resp = request(client, v, e2, at(10, 30), at(11, 30), "coord_b")

    assert resp.status_code == 409
    assert resp.json()["detail"] == f"Direct conflict with existing booking ({D.isoformat()} 10:00 - 12:00)"
    db_session.expire_all()
    assert db_session.query(VenueBooking).filter(VenueBooking.venue_id == v.id).count() == 1


def test_tc3817_turnaround_edge_against_a_confirmed_booking(client, db_session, e1, e2):
    """TC-3817 | Boundary Path | AC2
    Three venues, each with a Confirmed booking D 10:00-12:00 (occupied until
    12:45). coord_b requests one hour from 13:14, 13:15 and 13:16, one per venue
    (occupied from 12:44, 12:45 and 12:46).
    Expected: 409; 201 (the windows only touch); 201. Also proven by TC-3509 and TC-3803."""
    venues = [add_venue(db_session, name) for name in ("Hall One", "Hall Two", "Hall Three")]
    for venue in venues:
        confirmed(client, venue, e1, at(10), at(12), "coord_a")

    codes = [
        request(client, venue, e2, at(13, minute), at(14, minute), "coord_b").status_code
        for venue, minute in zip(venues, [14, 15, 16])
    ]

    assert codes == [409, 201, 201]


def test_tc3818_first_come_first_served(client, v, e1, e2):
    """TC-3818 | Workflow Variation | AC4
    coord_a requests D 10:00-12:00, then coord_b requests 11:00-13:00. Venue Staff
    reject coord_a's request with a reason; coord_b requests 11:00-13:00 again.
    Expected: 201; 409; then 201. Also proven by TC-3502, TC-3510 and TC-3612."""
    first = request(client, v, e1, at(10), at(12), "coord_a")
    assert first.status_code == 201
    assert request(client, v, e2, at(11), at(13), "coord_b").status_code == 409

    assert reject(client, first.json()["booking"]["booking_id"]).status_code == 200
    assert request(client, v, e2, at(11), at(13), "coord_b").status_code == 201


def test_tc3819_a_confirmed_booking_is_never_overridden(client, v, e1):
    """TC-3819 | Negative Path | AC4
    A Confirmed booking. Venue Staff approve it again, then reject it with a reason.
    Expected: 409; 409; it stays Confirmed. Also proven by TC-3606, TC-3607 and TC-3709."""
    booking_id = confirmed(client, v, e1, at(10), at(12), "coord_a")

    assert approve(client, booking_id).status_code == 409
    assert reject(client, booking_id).status_code == 409
    assert view(client, booking_id)["status"] == "Confirmed"


# ================================================================ what part 2 adds: the approval re-check


def test_tc3820_approval_refused_after_a_buffer_change_creates_a_clash(client, db_session, v, e1, e2):
    """TC-3820 | Negative Path | AC2, AC4 (D5, D7)
    A is Confirmed for D 10:00-12:00. B is requested for 13:30-15:00 (occupied
    13:00-15:45), so it is Pending. Venue Staff raise V's turnaround to 90 minutes,
    so A now occupies V until 13:30.
    Expected: approving B gives 409 "Cannot approve: Conflicts with booking
    (D 10:00 - 12:00) once 30m setup and 90m turnaround are included"; B stays
    Pending with no decision recorded; A is unchanged (Confirmed, same times)."""
    a = confirmed(client, v, e1, at(10), at(12), "coord_a")
    b = hold(client, v, e2, at(13, 30), at(15), "coord_b")
    a_before = view(client, a)
    set_turnaround(client, v, 90)

    resp = approve(client, b)

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        f"Cannot approve: Conflicts with booking ({D.isoformat()} 10:00 - 12:00) "
        "once 30m setup and 90m turnaround are included"
    )
    b_after = view(client, b)
    assert b_after["status"] == "Pending"
    assert b_after["decision"] is None
    db_session.expire_all()
    assert db_session.query(VenueBookingDecision).filter(VenueBookingDecision.booking_id == b).count() == 0
    a_after = view(client, a)
    for key in ("status", "booking_start", "booking_end", "decision"):
        assert a_after[key] == a_before[key], key


def test_tc3821_windows_that_only_touch_after_a_buffer_change_can_be_approved(client, v, e1, e2):
    """TC-3821 | Boundary Path | AC2 (D5)
    As TC-3820, but the turnaround is raised to 60 minutes: A ends at 13:00 and
    B starts at 13:00, so the windows only touch.
    Expected: approving B succeeds (200, Confirmed). Also proven by TC-8006."""
    confirmed(client, v, e1, at(10), at(12), "coord_a")
    b = hold(client, v, e2, at(13, 30), at(15), "coord_b")
    set_turnaround(client, v, 60)

    resp = approve(client, b)

    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "Confirmed"


def test_tc3822_two_holds_that_clash_after_a_buffer_change(client, v, e1, e2):
    """TC-3822 | Negative Path | AC4 (D5, D6)
    H1 (E1) is Pending for D 10:00-12:00 and H2 (E2) for 13:30-15:00; they didn't
    clash when requested. Venue Staff raise V's turnaround to 90 minutes.
    Expected: approving H1 gives 409 and approving H2 gives 409 (no automatic
    winner); rejecting H2 with a reason succeeds (200: a clash never blocks a
    reject); approving H1 then succeeds (200, Confirmed)."""
    h1 = hold(client, v, e1, at(10), at(12), "coord_a")
    h2 = hold(client, v, e2, at(13, 30), at(15), "coord_b")
    set_turnaround(client, v, 90)

    assert approve(client, h1).status_code == 409
    assert approve(client, h2).status_code == 409
    assert reject(client, h2).status_code == 200
    resp = approve(client, h1)
    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "Confirmed"


def test_tc3823_rejected_and_other_venue_bookings_do_not_stop_approval(client, db_session, v, e1, e2):
    """TC-3823 | Cross-Cutting | AC1, AC2 (D5)
    At V, X (E2) is requested for D 10:00-12:00 and rejected with a reason; B (E1)
    is then requested for the same period. At venue W, E2 has a Confirmed booking
    for the same period.
    Expected: approving B succeeds (200, Confirmed). Also proven by TC-8008,
    TC-3805 and TC-3806."""
    x = hold(client, v, e2, at(10), at(12), "coord_b")
    assert reject(client, x).status_code == 200
    b = hold(client, v, e1, at(10), at(12), "coord_a")
    w = add_venue(db_session, "Seminar Room")
    confirmed(client, w, e2, at(10), at(12), "coord_b")

    resp = approve(client, b)

    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "Confirmed"


def test_tc3824_an_expired_hold_does_not_stop_approval(client, clock, v, e1, e2):
    """TC-3824 | Cross-Cutting | AC2 (with SCRUM-81)
    X (E2) holds V for D 10:00-12:00; Venue Staff give it a 5-minute expiry and the
    clock moves 6 minutes, so X has expired. B (E1) is then requested for the same
    period (201).
    Expected: approving B succeeds (200, Confirmed). Also proven by TC-8104."""
    x = hold(client, v, e2, at(10), at(12), "coord_b")
    login(client, "venue1")
    resp = client.put(f"/venues/bookings/{x}/hold", json={"expires_at": (clock.now + timedelta(minutes=5)).isoformat()})
    assert resp.status_code == 200
    clock.now += timedelta(minutes=6)
    b = hold(client, v, e1, at(10), at(12), "coord_a")

    resp = approve(client, b)

    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "Confirmed"


def test_tc3825_exclude_booking_id_leaves_one_booking_out(db_session, v, e1):
    """TC-3825 | Happy Path | D7
    The only booking at V is b, D 10:00-12:00.
    Expected: check_venue_availability(V, D 10:00, D 12:00, db) gives (False,
    "Direct conflict with existing booking (D 10:00 - 12:00)"); with
    exclude_booking_id=b it gives (True, None). Calls without the argument are
    unchanged (TC-3809 and SCRUM-35's tests pass as they are)."""
    b = VenueBooking(venue_id=v.id, event_id=e1.id, booking_start=at(10), booking_end=at(12), status="Confirmed")
    db_session.add(b)
    db_session.commit()

    assert check_venue_availability(v, at(10), at(12), db_session) == (
        False, f"Direct conflict with existing booking ({D.isoformat()} 10:00 - 12:00)",
    )
    assert check_venue_availability(v, at(10), at(12), db_session, exclude_booking_id=b.id) == (True, None)


# ================================================================ PR 27 review: concurrency-safe approval
# Interleaving without threads: a hook wraps check_venue_availability, so the
# first time the approval under test calls it (its clash check, before it has
# written anything), the hook runs another session's work to completion. That
# is the window between an approval's checks and its commit.


def run_once_after_clash_check(monkeypatch, other_work):
    """Wrap venues.check_venue_availability: return the real answer, but the
    first time only, run other_work() first. An error in other_work is recorded
    in state["error"] rather than raised, so it can't pass for the refusal of
    the approval under test."""
    from backend import venues

    real = venues.check_venue_availability
    state = {"ran": False, "error": None}

    def hooked(*args, **kwargs):
        result = real(*args, **kwargs)
        if not state["ran"]:
            state["ran"] = True
            try:
                other_work()
            except Exception as error:  # noqa: BLE001 - recorded for the test to assert on
                state["error"] = error
        return result

    monkeypatch.setattr(venues, "check_venue_availability", hooked)
    return state


def decide_in_new_session(booking_id, decision, new_status, staff_username, reason=None):
    """One complete decision in its own session, as another request would make it."""
    from backend.venues import _decide_booking

    other = TestingSessionLocal()
    try:
        staff = other.query(User).filter(User.username == staff_username).one()
        return _decide_booking(booking_id, decision, new_status, staff, other, reason=reason)
    finally:
        other.close()


def request_in_new_session(venue_id, event_id, coordinator_username, start, end):
    """One complete booking request in its own session, as another request would make it."""
    from backend.venue_holds import run_hold_checks
    from backend.venues import VenueBookingRequest, submit_venue_booking_request

    other = TestingSessionLocal()
    try:
        run_hold_checks(db=other)  # the router-level hold check every venues request runs first
        coordinator = other.query(User).filter(User.username == coordinator_username).one()
        payload = VenueBookingRequest(event_id=event_id, booking_start=start, booking_end=end)
        return submit_venue_booking_request(venue_id, payload, coordinator, other)["booking"]["booking_id"]
    finally:
        other.close()


def test_tc3826_an_approval_overtaken_by_expiry_and_a_new_confirmation_is_refused(
    client, clock, db_session, monkeypatch, v, e1, e2
):
    """TC-3826 | Cross-Cutting | AC2, AC4 (PR 27 review: concurrent expiry and approval)
    A is a hold for E1 at V, D 10:00-12:00, with a 5-minute expiry. Venue Staff
    start approving A, and A passes its checks. Before A's approval commits: A's hold expires, B (E2) is
    requested for the same venue and period in another session, and B is
    approved there.
    Expected: A's approval gets 409 and A is not Confirmed; the slot ends with
    exactly one Confirmed booking (B)."""
    from fastapi import HTTPException

    from backend.venues import _decide_booking

    a = hold(client, v, e1, at(10), at(12), "coord_a")
    # A short expiry (rather than the 48-hour default): created_at uses the real
    # clock, so a hold requested after a 48-hour jump of the test clock (B) would
    # be born expired.
    expires = clock.now + timedelta(minutes=5)
    login(client, "venue1")
    assert client.put(f"/venues/bookings/{a}/hold", json={"expires_at": expires.isoformat()}).status_code == 200
    other_ids = {}

    def a_expires_and_b_is_confirmed():
        clock.now = expires + timedelta(minutes=1)
        b = request_in_new_session(v.id, e2.id, "coord_b", at(10), at(12))
        decide_in_new_session(b, "Approved", "Confirmed", "venue1")
        other_ids["b"] = b

    hook = run_once_after_clash_check(monkeypatch, a_expires_and_b_is_confirmed)
    staff = db_session.query(User).filter(User.username == "venue1").one()

    try:
        _decide_booking(a, "Approved", "Confirmed", staff, db_session)
        refused = None
    except HTTPException as error:
        refused = error

    assert hook["ran"] is True
    assert hook["error"] is None, repr(hook["error"])
    assert refused is not None and refused.status_code == 409, "A's approval was not refused"
    fresh = TestingSessionLocal()
    try:
        assert fresh.get(VenueBooking, a).status != "Confirmed"
        confirmed_ids = [
            b.id for b in fresh.query(VenueBooking).filter(VenueBooking.venue_id == v.id,
                                                           VenueBooking.status == "Confirmed")
        ]
        assert confirmed_ids == [other_ids["b"]]
    finally:
        fresh.close()


def test_tc3827_two_interleaved_approvals_of_one_booking_record_one_decision(
    client, db_session, monkeypatch, v, e1
):
    """TC-3827 | Cross-Cutting | AC4 (PR 27 review)
    A is a hold at V. Venue Staff start approving A, and A passes its checks.
    Before that approval commits, a second approval of A completes in another
    session.
    Expected: the first approval gets 409; A is Confirmed once, with exactly one
    decision row."""
    from fastapi import HTTPException

    from backend.venues import _decide_booking

    a = hold(client, v, e1, at(10), at(12), "coord_a")
    hook = run_once_after_clash_check(monkeypatch, lambda: decide_in_new_session(a, "Approved", "Confirmed", "venue1"))
    staff = db_session.query(User).filter(User.username == "venue1").one()

    try:
        _decide_booking(a, "Approved", "Confirmed", staff, db_session)
        refused = None
    except HTTPException as error:
        refused = error

    assert hook["ran"] is True
    assert hook["error"] is None, repr(hook["error"])
    assert refused is not None and refused.status_code == 409, "A's approval was not refused"
    fresh = TestingSessionLocal()
    try:
        assert fresh.get(VenueBooking, a).status == "Confirmed"
        assert fresh.query(VenueBookingDecision).filter(VenueBookingDecision.booking_id == a).count() == 1
    finally:
        fresh.close()
