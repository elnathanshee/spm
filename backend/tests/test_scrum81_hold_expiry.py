"""SCRUM-81: Tentative venue holds expire.

Acceptance criteria covered:
  AC1  Every hold has an expiry date and time.
  AC2  After expiry, the hold no longer blocks the venue and it becomes
       available to other requests.
  AC3  An expired hold is never shown or treated as a confirmed booking.
  AC4  The Coordinator is notified before or at expiry.
Also SCRUM-38 AC7: active tentative holds count as conflicts; expired holds don't.

PO decisions tested:
  D1  A hold is a Pending booking. Only holds expire; Confirmed never does.
  D2  Default expiry: 48 hours after the request, or the booking's start if
      sooner (team assumption, O-31). Unsaved bookings never count as expired.
  D3  PUT /venues/bookings/{id}/hold (Venue Staff): live hold only (409), new
      expiry in the future and no later than the start (422), others 403,
      unknown 404. Each change notifies the Coordinator; the warning is due again.
  D4  PUT /venues/bookings/{id}/hold/warning (assigned Coordinator only):
      warning_minutes >= 0 (422), others 403, expired 409. Default 24 hours
      (team assumption, O-33). Each warning and expiry notice is sent once; a
      changed warning time is due again (PO answer 3).
  D5  No background job: expiry is worked out from the clock. Expired holds
      don't block, can't be decided (409), and show as Expired; the check that
      sends notices and stores "Expired" runs at the start of requests on the
      events, notifications, venues and venue_holds routers (PO answers 1, 2).
  D7  One clock, booking_rules.utc_now (UTC without a time zone). Times sent
      with a time zone are converted to UTC.
  D8  Notices go to the event's assigned Coordinator only.

The clock: each test replaces booking_rules.utc_now with a clock that starts at
the real current UTC time (so it lines up with created_at, which uses the real
clock) and is moved forward from there. No test sleeps, and no date is fixed:
T is the clock at the start of the test and D is T plus 30 days.
"""

import os
import sys
import types
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import booking_rules, venue_holds
from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, Notification, User, Venue, VenueBooking, VenueHold
from backend.security import hash_password

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"
MINUTE = timedelta(minutes=1)
HOLD_KINDS = {"hold_expiry_set", "hold_warning", "hold_expired"}


def fmt(moment):
    """How notices and refusals write a time (the same style as the conflict messages)."""
    return moment.strftime("%Y-%m-%d %H:%M")


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
    """D7: the one clock, set by the test. clock.now starts at the real UTC now (T);
    clock.d is day D (T + 30 days), fixed when the clock is created."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    c = types.SimpleNamespace(now=now, d=(now + timedelta(days=30)).date())
    monkeypatch.setattr(booking_rules, "utc_now", lambda: c.now, raising=False)
    return c


@pytest.fixture
def users(db_session):
    """Token for each: tok_<username>."""
    created = {}
    for username, role, active in [
        ("org1", "organiser", False),
        ("coord_a", "coordinator", True),
        ("coord_b", "coordinator", True),
        ("coord_c", "coordinator", True),
        ("lead1", "lead", False),
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
    event = Event(
        title=title,
        expected_attendance=50,
        organiser_id=users["org1"].id,
        status="Approved",
        assigned_coordinator_id=users[coordinator].id if coordinator else None,
    )
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


def d_at(clock, hour, minute=0):
    """D (T + 30 days) at hour:minute. D is fixed at T, so moving the clock
    doesn't move the booked period."""
    return datetime.combine(clock.d, time(hour, minute))


def login(client, username):
    if username is None:
        client.cookies.clear()
    else:
        client.cookies.set(COOKIE, f"tok_{username}")


def request_hold(client, venue, event, start, end, coordinator="coord_a"):
    """A Coordinator requests the venue (SCRUM-35); returns the response."""
    login(client, coordinator)
    return client.post(
        f"/venues/{venue.id}/bookings",
        json={"event_id": event.id, "booking_start": start.isoformat(), "booking_end": end.isoformat()},
    )


def new_hold(client, venue, event, start, end, coordinator="coord_a"):
    resp = request_hold(client, venue, event, start, end, coordinator)
    assert resp.status_code == 201, resp.text
    return resp.json()["booking"]["booking_id"]


def view(client, booking_id):
    """Venue Staff's view of a request (GET /venues/bookings/{id})."""
    login(client, "venue1")
    return client.get(f"/venues/bookings/{booking_id}").json()


def expiry_of(client, booking_id):
    return datetime.fromisoformat(view(client, booking_id)["hold_expires_at"])


def set_expiry(client, booking_id, expires_at, user="venue1"):
    login(client, user)
    value = expires_at if isinstance(expires_at, str) else expires_at.isoformat()
    return client.put(f"/venues/bookings/{booking_id}/hold", json={"expires_at": value})


def set_warning(client, booking_id, minutes, user="coord_a"):
    login(client, user)
    return client.put(f"/venues/bookings/{booking_id}/hold/warning", json={"warning_minutes": minutes})


def notices(client, username, kind=None):
    """The user's notifications (this request also runs the hold check)."""
    login(client, username)
    rows = client.get("/notifications").json()
    return [n for n in rows if (n["kind"] == kind if kind else n["kind"] in HOLD_KINDS)]


# ================================================================ AC1: every hold has an expiry


def test_tc8101_a_new_hold_expires_48_hours_after_the_request(client, clock, v, e1):
    """TC-8101 | Happy Path | AC1 (D2)
    A Coordinator requests V for D 10:00-12:00 at T.
    Expected: Venue Staff's view shows hold_expires_at = created_at + 48 hours
    (created_at is when the request was made, between T and now) and
    warning_minutes 1440 (24 hours)."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    data = view(client, booking_id)

    expires = datetime.fromisoformat(data["hold_expires_at"])
    assert expires == datetime.fromisoformat(data["created_at"]) + timedelta(hours=48)
    assert clock.now <= datetime.fromisoformat(data["created_at"]) <= datetime.now(timezone.utc).replace(tzinfo=None)
    assert data["warning_minutes"] == 1440
    assert data["status"] == "Pending"


def test_tc8116_default_expiry_is_capped_at_the_booking_start(client, clock, v, e1):
    """TC-8116 | Boundary Path | AC1 (D2: or the booking's start, if sooner)
    A request for T + 24 h, one hour long (its start is sooner than 48 hours).
    Expected: hold_expires_at is the booking's start."""
    start = (clock.now + timedelta(hours=24)).replace(microsecond=0)
    booking_id = new_hold(client, v, e1, start, start + timedelta(hours=1))

    assert expiry_of(client, booking_id) == start


def test_tc8102_venue_staff_set_the_expiry_and_the_coordinator_is_told(client, clock, v, e1):
    """TC-8102 | Happy Path | AC1 (D3)
    Venue Staff set the hold's expiry to T + 5 days.
    Expected: 200; the view shows the new expiry; E1's Coordinator (coord_a) has
    exactly one hold_expiry_set notice naming V, the period and the new expiry."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    new_expiry = (clock.now + timedelta(days=5)).replace(second=0, microsecond=0)

    resp = set_expiry(client, booking_id, new_expiry)

    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["booking"]["hold_expires_at"]) == new_expiry
    assert expiry_of(client, booking_id) == new_expiry
    [notice] = notices(client, "coord_a", "hold_expiry_set")
    assert "Grand Hall" in notice["message"]
    # [SCRUM-95] The booked period is written in Singapore time: D 10:00 UTC is 18:00.
    assert f"{fmt(d_at(clock, 18))} - 20:00" in notice["message"]
    assert fmt(new_expiry) in notice["message"]


def test_tc8103_setting_the_expiry_is_refused_when_invalid(client, clock, v, e1, users):
    """TC-8103 | Negative Path | D3
    Expected: an expiry in the past gives 422; one later than D 10:00 (D 10:01)
    gives 422; a Coordinator or an Organiser trying gets 403; booking 99999 gives 404."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    assert set_expiry(client, booking_id, clock.now - timedelta(hours=1)).status_code == 422
    assert set_expiry(client, booking_id, d_at(clock, 10, 1)).status_code == 422
    future = clock.now + timedelta(days=2)
    assert set_expiry(client, booking_id, future, user="coord_a").status_code == 403
    assert set_expiry(client, booking_id, future, user="org1").status_code == 403
    assert set_expiry(client, 99999, future).status_code == 404


def test_tc8117_new_expiry_limits_now_and_the_booking_start(client, clock, v, e1):
    """TC-8117 | Boundary Path | D3 (in the future, no later than the start)
    A hold for D 10:00-12:00.
    Expected: exactly now gives 422; now + 1 minute gives 200; exactly D 10:00
    gives 200; D 10:01 gives 422."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    assert set_expiry(client, booking_id, clock.now).status_code == 422
    assert set_expiry(client, booking_id, clock.now + MINUTE).status_code == 200
    assert set_expiry(client, booking_id, d_at(clock, 10)).status_code == 200
    assert set_expiry(client, booking_id, d_at(clock, 10, 1)).status_code == 422


def test_tc8118_an_expiry_with_a_time_zone_is_converted_to_utc(client, clock, v, e1):
    """TC-8118 | Boundary Path | D7
    Venue Staff send an expiry of 18:00 at +08:00, two days from T.
    Expected: it is stored and shown as 10:00 UTC, without a time zone."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    day = (clock.now + timedelta(days=2)).date()

    resp = set_expiry(client, booking_id, f"{day.isoformat()}T18:00:00+08:00")

    assert resp.status_code == 200
    assert resp.json()["booking"]["hold_expires_at"] == datetime.combine(day, time(10)).isoformat()


# ================================================================ AC2: an expired hold stops blocking


def test_tc8104_expiry_edge_for_new_requests(client, clock, db_session, e1, e2):
    """TC-8104 | Boundary Path | AC2 (SCRUM-38 AC7)
    Three venues, each with its own default hold for D 10:00-12:00 (expiry X).
    Another event's Coordinator requests the same period: at X - 1 minute on the
    first, at exactly X on the second, at X + 1 minute on the third.
    Expected: 409 (the hold still blocks); 201; 201."""
    venues = [add_venue(db_session, name) for name in ("Hall One", "Hall Two", "Hall Three")]
    holds = [new_hold(client, venue, e1, d_at(clock, 10), d_at(clock, 12)) for venue in venues]
    expected = [409, 201, 201]

    for venue, booking_id, offset, code in zip(venues, holds, [-MINUTE, timedelta(0), MINUTE], expected):
        clock.now = expiry_of(client, booking_id) + offset
        resp = request_hold(client, venue, e2, d_at(clock, 10), d_at(clock, 12), coordinator="coord_b")
        assert resp.status_code == code, (venue.name, offset)


def test_tc8105_after_expiry_search_calendar_and_retire_treat_the_venue_as_free(client, clock, v, e1):
    """TC-8105 | Happy Path | AC2
    A hold for D 10:00-12:00; the clock moves past its expiry.
    Expected: search for D 10:00-12:00 shows V available; the calendar for D
    shows no busy block; retiring V succeeds with no affected bookings."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    clock.now = expiry_of(client, booking_id) + MINUTE

    login(client, "coord_a")
    params = {"start_time": d_at(clock, 10).isoformat(), "end_time": d_at(clock, 12).isoformat()}
    [row] = [x for x in client.get("/venues/search", params=params).json() if x["name"] == "Grand Hall"]
    assert row["is_available"] is True
    day = d_at(clock, 0).date().isoformat()
    calendar = client.get(f"/venues/{v.id}/calendar", params={"from": day, "to": day}).json()
    assert calendar["days"][0]["busy"] == []

    login(client, "venue1")
    resp = client.post(f"/venues/{v.id}/retire")
    assert resp.status_code == 200
    assert resp.json()["affected_bookings"] == []


def test_tc8108_first_come_first_served_after_expiry(client, clock, v, e1, e2):
    """TC-8108 | Workflow Variation | AC2
    coord_a's hold for D 10:00-12:00 is given a 5-minute expiry by Venue Staff,
    and the clock moves 6 minutes, so it expires. (A short expiry rather than the
    48-hour default, because created_at uses the real clock: a hold requested
    after the test clock had jumped 48 hours would be born expired.)
    Expected: coord_b's request for the same period (E2) gets 201; coord_a's
    new request for it (E1) then gets 409, because coord_b's hold now blocks."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    assert set_expiry(client, booking_id, clock.now + 5 * MINUTE).status_code == 200
    clock.now += 6 * MINUTE

    assert request_hold(client, v, e2, d_at(clock, 10), d_at(clock, 12), coordinator="coord_b").status_code == 201
    assert request_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12), coordinator="coord_a").status_code == 409


# ================================================================ AC3: never shown or treated as confirmed


def test_tc8106_an_expired_hold_cannot_be_decided_and_shows_as_expired(client, clock, v, e1):
    """TC-8106 | Negative Path | AC3 (D5)
    A hold whose expiry has passed.
    Expected: approving gives 409, saying it expired at its expiry time and the
    Coordinator must submit a new request; rejecting (with a reason) gives 409
    too; it never becomes Confirmed; GET /venues/bookings/{id},
    GET /venues/{id}/bookings and GET /events/{id}/bookings all show it as Expired."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    expiry = expiry_of(client, booking_id)
    clock.now = expiry + MINUTE

    login(client, "venue1")
    approve = client.post(f"/venues/bookings/{booking_id}/approve")
    assert approve.status_code == 409
    assert "expired" in approve.json()["detail"].lower()
    assert fmt(expiry) in approve.json()["detail"]
    assert "new request" in approve.json()["detail"].lower()
    reject = client.post(f"/venues/bookings/{booking_id}/reject", json={"reason": "No longer needed"})
    assert reject.status_code == 409

    assert view(client, booking_id)["status"] == "Expired"
    login(client, "venue1")
    [listed] = client.get(f"/venues/{v.id}/bookings").json()
    assert listed["status"] == "Expired"
    login(client, "coord_a")
    [for_event] = client.get(f"/events/{e1.id}/bookings").json()
    assert for_event["status"] == "Expired"


def test_tc8107_an_expired_hold_cannot_be_extended(client, clock, v, e1):
    """TC-8107 | Negative Path | AC3 (D3)
    A hold whose expiry has passed; Venue Staff try to extend it by 3 days.
    Expected: 409."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    clock.now = expiry_of(client, booking_id) + MINUTE

    assert set_expiry(client, booking_id, clock.now + timedelta(days=3)).status_code == 409


def test_tc8113_a_confirmed_hold_never_expires(client, clock, v, e1, e2):
    """TC-8113 | Cross-Cutting | AC3 (D1)
    A hold approved before its expiry; the clock then moves past the old expiry.
    Expected: another event's request for the same period gets 409; the view
    shows Confirmed with hold_expires_at null; coord_a gets no hold_expired
    notice; Venue Staff can't set an expiry on it (409, D3: only a Pending hold)."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    old_expiry = expiry_of(client, booking_id)
    login(client, "venue1")
    assert client.post(f"/venues/bookings/{booking_id}/approve").status_code == 200

    clock.now = old_expiry + timedelta(hours=1)

    assert request_hold(client, v, e2, d_at(clock, 10), d_at(clock, 12), coordinator="coord_b").status_code == 409
    data = view(client, booking_id)
    assert data["status"] == "Confirmed"
    assert data["hold_expires_at"] is None
    assert notices(client, "coord_a", "hold_expired") == []
    assert set_expiry(client, booking_id, clock.now + timedelta(days=2)).status_code == 409


def test_tc8120_views_show_the_hold_and_the_status_filter_follows_expiry(client, clock, v, e1, e2):
    """TC-8120 | Negative Path | AC3 (D5, D8; PO answer 1)
    At V: E1's default hold for D 10:00-12:00, and E2's hold for D 14:00-16:00
    extended to T + 10 days. The clock moves just past E1's hold's expiry.
    Expected: the three views show hold_expires_at and warning_minutes (1440) for
    E1's hold with status Expired; GET /venues/{id}/bookings?status=Expired
    lists only E1's hold, and ?status=Pending only E2's."""
    expired_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    live_id = new_hold(client, v, e2, d_at(clock, 14), d_at(clock, 16), coordinator="coord_b")
    assert set_expiry(client, live_id, clock.now + timedelta(days=10)).status_code == 200
    expiry = expiry_of(client, expired_id)
    clock.now = expiry + MINUTE

    staff_view = view(client, expired_id)
    login(client, "coord_a")
    [event_view] = client.get(f"/events/{e1.id}/bookings").json()
    for data in [staff_view, event_view]:
        assert data["status"] == "Expired"
        assert datetime.fromisoformat(data["hold_expires_at"]) == expiry
        assert data["warning_minutes"] == 1440

    login(client, "venue1")
    statuses = {b["booking_id"]: b["status"] for b in client.get(f"/venues/{v.id}/bookings").json()}
    assert statuses == {expired_id: "Expired", live_id: "Pending"}
    expired_only = client.get(f"/venues/{v.id}/bookings", params={"status": "Expired"}).json()
    pending_only = client.get(f"/venues/{v.id}/bookings", params={"status": "Pending"}).json()
    assert [b["booking_id"] for b in expired_only] == [expired_id]
    assert [b["booking_id"] for b in pending_only] == [live_id]


# ================================================================ AC4: the Coordinator is warned


def test_tc8109_a_chosen_warning_time_then_the_expiry_notice_each_once(client, clock, v, e1):
    """TC-8109 | Happy Path | AC4 (D4)
    coord_a sets a 120-minute warning on a hold expiring at X.
    Expected: at X - 121 min no warning; at X - 120 min exactly one; at X - 1 min
    still one; at X one hold_expired notice; at X + 10 min still one of each."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    assert set_warning(client, booking_id, 120).status_code == 200
    x = expiry_of(client, booking_id)

    expectations = [
        (x - timedelta(minutes=121), 0, 0),
        (x - timedelta(minutes=120), 1, 0),
        (x - MINUTE, 1, 0),
        (x, 1, 1),
        (x + timedelta(minutes=10), 1, 1),
    ]
    for moment, warnings, expired in expectations:
        clock.now = moment
        assert len(notices(client, "coord_a", "hold_warning")) == warnings, moment
        assert len(notices(client, "coord_a", "hold_expired")) == expired, moment


def test_tc8110_default_24_hour_warning_edge(client, clock, v, e1):
    """TC-8110 | Boundary Path | AC4 (D4 default)
    A hold with the default warning, expiring at X.
    Expected: no warning at X - 24 h - 1 min; exactly one at X - 24 h."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    x = expiry_of(client, booking_id)

    clock.now = x - timedelta(hours=24) - MINUTE
    assert notices(client, "coord_a", "hold_warning") == []
    clock.now = x - timedelta(hours=24)
    assert len(notices(client, "coord_a", "hold_warning")) == 1


def test_tc8111_setting_the_warning_is_refused_when_invalid(client, clock, v, e1):
    """TC-8111 | Negative Path | D4
    Expected: another Coordinator (coord_b), Venue Staff and the Lead get 403;
    a negative warning (-1) gives 422; a warning on an expired hold gives 409."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    for user in ["coord_b", "venue1", "lead1"]:
        assert set_warning(client, booking_id, 60, user=user).status_code == 403, user
    assert set_warning(client, booking_id, -1).status_code == 422

    clock.now = expiry_of(client, booking_id) + MINUTE
    assert set_warning(client, booking_id, 60).status_code == 409


def test_tc8112_extending_after_the_warning_brings_a_new_warning(client, clock, v, e1):
    """TC-8112 | Workflow Variation | D3, D4
    The default warning has gone out (clock at X - 24 h). Venue Staff then extend
    the expiry to X + 3 days.
    Expected: coord_a gets a hold_expiry_set notice; no new warning at
    new X - 24 h - 1 min; a second warning at new X - 24 h."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    x = expiry_of(client, booking_id)
    clock.now = x - timedelta(hours=24)
    assert len(notices(client, "coord_a", "hold_warning")) == 1

    new_x = (x + timedelta(days=3)).replace(microsecond=0)
    assert set_expiry(client, booking_id, new_x).status_code == 200
    assert len(notices(client, "coord_a", "hold_expiry_set")) == 1

    clock.now = new_x - timedelta(hours=24) - MINUTE
    assert len(notices(client, "coord_a", "hold_warning")) == 1
    clock.now = new_x - timedelta(hours=24)
    assert len(notices(client, "coord_a", "hold_warning")) == 2


def test_tc8121_a_changed_warning_time_is_due_again(client, clock, v, e1):
    """TC-8121 | Workflow Variation | D4 (PO answer 3)
    A 120-minute warning has gone out; coord_a then changes it to 30 minutes.
    Expected: a second warning at X - 30 min."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    assert set_warning(client, booking_id, 120).status_code == 200
    x = expiry_of(client, booking_id)
    clock.now = x - timedelta(minutes=120)
    assert len(notices(client, "coord_a", "hold_warning")) == 1

    assert set_warning(client, booking_id, 30).status_code == 200
    clock.now = x - timedelta(minutes=31)
    assert len(notices(client, "coord_a", "hold_warning")) == 1
    clock.now = x - timedelta(minutes=30)
    assert len(notices(client, "coord_a", "hold_warning")) == 2


def test_tc8123_a_zero_minute_warning_sends_only_the_expiry_notice(client, clock, v, e1):
    """TC-8123 | Boundary Path | AC4 (D4: if expired, only the expiry notice)
    coord_a sets the warning to 0 minutes.
    Expected: nothing at X - 1 min; at X only the hold_expired notice, no warning."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    assert set_warning(client, booking_id, 0).status_code == 200
    x = expiry_of(client, booking_id)

    clock.now = x - MINUTE
    assert notices(client, "coord_a") == []
    clock.now = x
    assert [n["kind"] for n in notices(client, "coord_a")] == ["hold_expired"]


# ================================================================ cross-cutting


def test_tc8114_only_the_assigned_coordinator_receives_hold_notices(client, clock, v, e1):
    """TC-8114 | Cross-Cutting | AC4 (D8)
    E1 (assigned to coord_a) has a hold. Venue Staff extend it, then the clock
    passes its warning and its expiry.
    Expected: coord_a has hold_expiry_set, hold_warning and hold_expired;
    coord_b, the Organiser, Venue Staff and the Lead have no hold notices."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    new_x = (clock.now + timedelta(days=3)).replace(microsecond=0)
    assert set_expiry(client, booking_id, new_x).status_code == 200
    clock.now = new_x - timedelta(hours=24)
    notices(client, "coord_a")
    clock.now = new_x

    assert sorted(n["kind"] for n in notices(client, "coord_a")) == ["hold_expired", "hold_expiry_set", "hold_warning"]
    for user in ["coord_b", "org1", "venue1", "lead1"]:
        assert notices(client, user) == [], user


def test_tc8119_holds_with_no_entitled_coordinator_notify_nobody(client, clock, db_session, users, v):
    """TC-8119 | Cross-Cutting | D8
    E3 has no assigned Coordinator, so the Lead requests its hold (since SCRUM-18,
    a Coordinator may request only for events assigned to them). E4 is assigned
    to coord_c, who requests its hold; coord_c's role is then changed to
    organiser (so coord_c is no longer entitled to see E4). Venue Staff extend
    both holds, and the clock passes both expiries.
    Expected: every request succeeds (200), and no hold notice is stored for anyone."""
    e3 = add_event(db_session, users, "Unassigned Talk", None)
    e4 = add_event(db_session, users, "Orphaned Talk", "coord_c")
    h3 = new_hold(client, v, e3, d_at(clock, 9), d_at(clock, 10), coordinator="lead1")
    h4 = new_hold(client, v, e4, d_at(clock, 14), d_at(clock, 15), coordinator="coord_c")
    coord_c = db_session.get(User, users["coord_c"].id)
    coord_c.role = "organiser"
    db_session.commit()

    new_x = (clock.now + timedelta(days=3)).replace(microsecond=0)
    assert set_expiry(client, h3, new_x).status_code == 200
    assert set_expiry(client, h4, new_x).status_code == 200
    clock.now = new_x + MINUTE
    login(client, "coord_a")
    assert client.get("/notifications").status_code == 200

    db_session.expire_all()
    assert db_session.query(Notification).filter(Notification.kind.in_(HOLD_KINDS)).count() == 0


def test_tc8122_no_session_cannot_set_expiry_or_warning(client, clock, v, e1):
    """TC-8122 | Cross-Cutting | D3, D4
    No session cookie.
    Expected: PUT /hold gives 401; PUT /hold/warning gives 401."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    assert set_expiry(client, booking_id, clock.now + timedelta(days=2), user=None).status_code == 401
    assert set_warning(client, booking_id, 60, user=None).status_code == 401


def test_tc8115_an_unsaved_hold_is_not_expired(clock):
    """TC-8115 | Cross-Cutting | D2
    A Pending booking that hasn't been saved (no created_at).
    Expected: is_expired_hold False, is_blocking True, hold_expires_at None.
    (Part 1's and SCRUM-35's tests passing unchanged is checked by the full suite.)"""
    booking = VenueBooking(
        status="Pending",
        booking_start=clock.now + timedelta(days=30),
        booking_end=clock.now + timedelta(days=30, hours=2),
    )

    assert booking_rules.is_expired_hold(booking) is False
    assert booking_rules.is_blocking(booking) is True
    assert booking_rules.hold_expires_at(booking) is None


# ================================================================ PR 26 review fixes (TC-8124 onward)
# New functions (hold_store, venue_holds.check_hold) are looked up inside the
# tests, so the file still imports while they don't exist (RED).


def stored_row(db_session, booking_id):
    db_session.expire_all()
    return db_session.query(VenueHold).filter(VenueHold.booking_id == booking_id).one()


def old_style_hold(db_session, venue, event, clock, start, end):
    """A Pending booking with no venue_holds row, like rows made before review item 3."""
    booking = VenueBooking(venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end,
                           status="Pending", created_at=clock.now)
    db_session.add(booking)
    db_session.commit()
    return booking


def hold_notices(db_session, kind):
    db_session.expire_all()
    return db_session.query(Notification).filter(Notification.kind == kind).count()


def test_tc8124_a_request_stores_its_default_expiry(client, clock, db_session, v, e1):
    """TC-8124 | Happy Path | AC1 (PR 26 review item 3)
    coord_a requests V for D 10:00-12:00.
    Expected: the request has a venue_holds row with expires_at =
    min(created_at + 48 h, booking_start) and no "set by" (the default); the
    view shows the same expiry."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    row = stored_row(db_session, booking_id)
    booking = db_session.get(VenueBooking, booking_id)
    assert row.expires_at == min(booking.created_at + timedelta(hours=48), booking.booking_start)
    assert row.expires_set_by_id is None
    assert expiry_of(client, booking_id) == row.expires_at


def test_tc8125_a_stored_default_is_capped_at_the_booking_start(client, clock, db_session, v, e1):
    """TC-8125 | Boundary Path | AC1 (PR 26 review item 3)
    A request starting at T + 24 h, one hour long.
    Expected: the stored expires_at is the booking's start."""
    start = (clock.now + timedelta(hours=24)).replace(microsecond=0)
    booking_id = new_hold(client, v, e1, start, start + timedelta(hours=1))

    assert stored_row(db_session, booking_id).expires_at == start


def test_tc8126_a_rule_change_does_not_move_a_stored_expiry(client, clock, db_session, monkeypatch, v, e1):
    """TC-8126 | Cross-Cutting | AC1 (PR 26 review item 3)
    One hold made through the request endpoint (expiry stored), and one inserted
    directly with no venue_holds row (like data from before this change). The
    48-hour rule is then changed to 24 hours for this test.
    Expected: the stored hold still expires at created_at + 48 h; the old-style
    hold uses the computed fallback, created_at + 24 h."""
    stored_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    old = old_style_hold(db_session, v, e1, clock, d_at(clock, 14), d_at(clock, 15))
    stored_created = db_session.get(VenueBooking, stored_id).created_at
    old_created = old.created_at
    monkeypatch.setattr(booking_rules, "DEFAULT_HOLD_HOURS", 24)

    assert expiry_of(client, stored_id) == stored_created + timedelta(hours=48)
    assert expiry_of(client, old.id) == old_created + timedelta(hours=24)


def test_tc8127_extending_updates_the_same_row(client, clock, db_session, v, e1, users):
    """TC-8127 | Workflow Variation | D3 (PR 26 review item 3)
    A hold with its stored default; Venue Staff extend it to T + 5 days.
    Expected: still exactly one venue_holds row, the one the request created,
    with expires_at T + 5 days and expires_set_by_id = venue1."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    first_row_id = stored_row(db_session, booking_id).id
    new_expiry = (clock.now + timedelta(days=5)).replace(microsecond=0)

    assert set_expiry(client, booking_id, new_expiry).status_code == 200

    db_session.expire_all()
    rows = db_session.query(VenueHold).filter(VenueHold.booking_id == booking_id).all()
    assert [r.id for r in rows] == [first_row_id]
    assert rows[0].expires_at == new_expiry
    assert rows[0].expires_set_by_id == users["venue1"].id


def test_tc8128_two_requests_creating_the_hold_row_at_once(clock, db_session, v, e1):
    """TC-8128 | Cross-Cutting | PR 26 review item 1
    An old-style hold with no venue_holds row; session A has loaded it. Session B
    creates the row and commits; then A asks for the row.
    Expected: no error; A gets B's row; exactly one row."""
    from backend import hold_store

    booking = old_style_hold(db_session, v, e1, clock, d_at(clock, 10), d_at(clock, 12))
    assert booking.hold is None  # A has loaded it: no row yet
    other = TestingSessionLocal()
    theirs = VenueHold(booking_id=booking.id, warning_minutes=60)
    other.add(theirs)
    other.commit()
    theirs_id = theirs.id
    other.close()

    hold = hold_store.get_or_create_hold(db_session, booking)

    assert hold.id == theirs_id
    assert hold.warning_minutes == 60
    assert db_session.query(VenueHold).filter(VenueHold.booking_id == booking.id).count() == 1


def test_tc8129_the_warning_is_sent_once_when_two_checks_overlap(client, clock, db_session, v, e1):
    """TC-8129 | Cross-Cutting | PR 26 review item 1
    A hold; the clock is at its warning time (X - 24 h). Session A has loaded the
    hold (warning not yet sent). Session B runs the hold check and commits; then
    A runs the hold check with what it loaded.
    Expected: exactly one hold_warning notice, and no error."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    x = expiry_of(client, booking_id)
    clock.now = x - timedelta(hours=24)
    db_session.expire_all()
    booking = db_session.get(VenueBooking, booking_id)
    assert booking.hold is None or booking.hold.warning_sent_at is None  # A has loaded it, unsent

    other = TestingSessionLocal()
    venue_holds.run_hold_checks(db=other)
    other.close()
    venue_holds.run_hold_checks(db=db_session)

    assert hold_notices(db_session, "hold_warning") == 1


def test_tc8130_the_expiry_notice_is_sent_once_when_two_checks_overlap(client, clock, db_session, v, e1):
    """TC-8130 | Cross-Cutting | PR 26 review item 1
    A hold; the clock is at its expiry X. Session A has loaded the hold (still
    Pending). Session B runs the hold check and commits; then A checks the hold
    it loaded.
    Expected: A reports no change; exactly one hold_expired notice; status Expired."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    clock.now = expiry_of(client, booking_id)
    db_session.expire_all()
    booking = db_session.get(VenueBooking, booking_id)
    assert booking.status == "Pending"  # A has loaded it

    other = TestingSessionLocal()
    venue_holds.run_hold_checks(db=other)
    other.close()
    changed = venue_holds.check_hold(db_session, booking, clock.now)

    assert changed is False
    assert hold_notices(db_session, "hold_expired") == 1
    assert db_session.get(VenueBooking, booking_id).status == "Expired"


def test_tc8131_storing_the_default_reuses_a_row_that_already_exists(clock, db_session, v, e1):
    """TC-8131 | Cross-Cutting | PR 26 review items 1 and 3
    A booking whose venue_holds row was created first by another session, with an
    expiry already set (T + 3 days); then its default expiry is stored.
    Expected: no error; the existing expiry is kept; exactly one row."""
    from backend import hold_store

    booking = old_style_hold(db_session, v, e1, clock, d_at(clock, 10), d_at(clock, 12))
    assert booking.hold is None
    kept = (clock.now + timedelta(days=3)).replace(microsecond=0)
    other = TestingSessionLocal()
    other.add(VenueHold(booking_id=booking.id, expires_at=kept))
    other.commit()
    other.close()

    hold_store.store_default_expiry(db_session, booking)

    row = stored_row(db_session, booking.id)
    assert row.expires_at == kept
    assert db_session.query(VenueHold).filter(VenueHold.booking_id == booking.id).count() == 1


def test_tc8132_the_coordinators_own_poll_delivers_notices_on_time(client, clock, v, e1):
    """TC-8132 | Happy Path | AC4 (PR 26 review item 2)
    A default hold expiring at X. Nothing else happens except coord_a's own
    GET /notifications, at exactly X - 24 h and then at exactly X.
    Expected: the response at X - 24 h includes hold_warning; the response at X
    includes hold_expired."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    x = expiry_of(client, booking_id)
    login(client, "coord_a")

    clock.now = x - timedelta(hours=24)
    assert "hold_warning" in [n["kind"] for n in client.get("/notifications").json()]
    clock.now = x
    assert "hold_expired" in [n["kind"] for n in client.get("/notifications").json()]


def test_tc8133_with_no_use_between_warning_and_expiry_only_the_expiry_notice_shows(client, clock, v, e1):
    """TC-8133 | Boundary Path | AC4 (PR 26 review item 2; the limitation in D4)
    A default hold expiring at X; nobody uses the app between the warning time
    and the expiry. coord_a's first GET /notifications is at X + 1 minute.
    Expected: only hold_expired, with no warning."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    clock.now = expiry_of(client, booking_id) + MINUTE

    assert [n["kind"] for n in notices(client, "coord_a")] == ["hold_expired"]


# ================================================================ PR 26 review, round 2 (TC-8134 onward)


def make_expiry_write_fail(monkeypatch):
    """Stand in for a failed write to venue_holds: creating the row raises."""
    from sqlalchemy.exc import OperationalError

    from backend import hold_store

    def failing(db, booking):
        raise OperationalError("INSERT INTO venue_holds", {}, Exception("simulated write failure"))

    monkeypatch.setattr(hold_store, "get_or_create_hold", failing)


def counts_from_a_fresh_session():
    """(bookings, venue_holds rows), read by a new session."""
    fresh = TestingSessionLocal()
    try:
        return fresh.query(VenueBooking).count(), fresh.query(VenueHold).count()
    finally:
        fresh.close()


def request_without_raising(clock, venue, event):
    """coord_a requests D 10:00-12:00 through a client that returns the 500
    instead of raising it."""
    with TestClient(app, raise_server_exceptions=False) as other_client:
        other_client.cookies.set(COOKIE, "tok_coord_a")
        return other_client.post(
            f"/venues/{venue.id}/bookings",
            json={"event_id": event.id, "booking_start": d_at(clock, 10).isoformat(),
                  "booking_end": d_at(clock, 12).isoformat()},
        )


def test_tc8134_a_failed_expiry_write_rolls_back_the_whole_request(client, clock, monkeypatch, v, e1):
    """TC-8134 | Negative Path | PR 26 review item 3, round 2
    The write of the default expiry fails (creating the venue_holds row raises).
    coord_a requests V for D 10:00-12:00.
    Expected: 500; read from a fresh session, no booking and no venue_holds row
    remain (the booking and its expiry are saved together or not at all)."""
    make_expiry_write_fail(monkeypatch)

    resp = request_without_raising(clock, v, e1)

    assert resp.status_code == 500
    assert counts_from_a_fresh_session() == (0, 0)


def test_tc8135_a_retry_after_the_failure_succeeds(client, clock, db_session, monkeypatch, v, e1):
    """TC-8135 | Workflow Variation | PR 26 review item 3, round 2
    A first request fails because the expiry write fails; the write is then
    restored and coord_a sends the same request again.
    Expected: 201; exactly one booking and one venue_holds row, whose expiry is
    the default (min(created_at + 48 h, booking_start))."""
    make_expiry_write_fail(monkeypatch)
    assert request_without_raising(clock, v, e1).status_code == 500
    monkeypatch.undo()

    resp = request_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))

    assert resp.status_code == 201, resp.text
    assert counts_from_a_fresh_session() == (1, 1)
    row = stored_row(db_session, resp.json()["booking"]["booking_id"])
    booking = db_session.get(VenueBooking, row.booking_id)
    assert row.expires_at == min(booking.created_at + timedelta(hours=48), booking.booking_start)


def test_tc8136_store_default_expiry_leaves_the_commit_to_the_caller(clock, db_session, v, e1):
    """TC-8136 | Cross-Cutting | PR 26 review item 3, round 2
    As the request endpoint does: a new Pending booking is added and flushed
    (not committed), store_default_expiry is called, then the caller rolls back.
    Expected: read from a fresh session, neither the booking nor a venue_holds
    row exists: store_default_expiry did not commit."""
    from backend import hold_store

    booking = VenueBooking(venue_id=v.id, event_id=e1.id, booking_start=d_at(clock, 10),
                           booking_end=d_at(clock, 12), status="Pending")
    db_session.add(booking)
    db_session.flush()

    hold_store.store_default_expiry(db_session, booking)
    db_session.rollback()

    assert counts_from_a_fresh_session() == (0, 0)

@pytest.mark.parametrize("after_expiry", [timedelta(0), timedelta(seconds=1)])
def test_tc9513_dialog_refresh_persists_expiry_and_notice(client, clock, db_session, v, e1, after_expiry):
    """TC-9513 (SCRUM-95 AC1, Workflow Variation): the dialog's GET itself must expire the hold, without a prior notification poll."""
    booking_id = new_hold(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    expiry = clock.now + timedelta(minutes=5)
    assert set_expiry(client, booking_id, expiry).status_code == 200
    assert set_warning(client, booking_id, 0).status_code == 200
    login(client, "coord_a")
    clock.now = expiry - timedelta(microseconds=1)
    before = client.get(f"/events/{e1.id}/bookings")
    assert before.status_code == 200
    assert before.json()[0]["status"] == "Pending"

    clock.now = expiry + after_expiry
    response = client.get(f"/events/{e1.id}/bookings")
    assert response.status_code == 200
    [booking] = response.json()
    assert booking["id"] == booking_id
    assert booking["status"] == "Expired"
    assert datetime.fromisoformat(booking["hold_expires_at"]) == expiry
    assert booking["warning_minutes"] == 0
    # Read a fresh session before calling any other API: serialization alone
    # must not conceal a missing transition or missing expiry notification.
    with TestingSessionLocal() as fresh:
        assert fresh.get(VenueBooking, booking_id).status == "Expired"
        hold = fresh.query(VenueHold).filter_by(booking_id=booking_id).one()
        assert hold.expires_at == expiry
        assert hold.expiry_notice_sent_at == clock.now
        notices = fresh.query(Notification).filter_by(event_id=e1.id, kind="hold_expired").all()
        assert len(notices) == 1
        assert notices[0].user_id == e1.assigned_coordinator_id
        assert "Grand Hall" in notices[0].message

    denied = set_warning(client, booking_id, 10)
    assert denied.status_code == 409
    assert "expired" in denied.json()["detail"]
    assert client.get(f"/events/{e1.id}/bookings").json()[0]["status"] == "Expired"
    with TestingSessionLocal() as fresh:
        assert fresh.query(Notification).filter_by(event_id=e1.id, kind="hold_expired").count() == 1
        assert fresh.query(VenueHold).filter_by(booking_id=booking_id).one().warning_minutes == 0


# SCRUM-95 AC1: booking times follow D7 too. The dialog sends the Coordinator's
# local time with its offset; it must be stored in UTC, or the default expiry
# (capped at the booking's start) and the Venue Staff limit compare a local
# start with UTC times. Singapore (+08:00) is the team's time zone.
SGT = "+08:00"


def sgt(moment):
    """A naive Singapore wall time, written with its offset as the dialog sends it."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + SGT


def request_sgt(client, venue, event, start, end, coordinator="coord_a"):
    login(client, coordinator)
    return client.post(
        f"/venues/{venue.id}/bookings",
        json={"event_id": event.id, "booking_start": sgt(start), "booking_end": sgt(end)},
    )


def test_tc9514_booking_times_sent_with_an_offset_are_stored_in_utc(client, clock, v, e1):
    """TC-9514 (SCRUM-95 AC1, D7, Happy Path): D 10:00-12:00 in Singapore is
    stored and returned as D 02:00-04:00 UTC."""
    resp = request_sgt(client, v, e1, d_at(clock, 10), d_at(clock, 12))
    assert resp.status_code == 201, resp.text
    booking = view(client, resp.json()["booking"]["booking_id"])
    assert datetime.fromisoformat(booking["booking_start"]) == d_at(clock, 2)
    assert datetime.fromisoformat(booking["booking_end"]) == d_at(clock, 4)


def test_tc9515_short_notice_hold_expires_at_the_real_booking_start(client, clock, v, e1):
    """TC-9515 (SCRUM-95 AC1, D2, Boundary Path): a booking 12 hours away is
    capped at its start. Sent at +08:00, the expiry is that start in UTC, not
    the Singapore wall time read as UTC (8 hours after the booking began)."""
    start_utc = (clock.now + timedelta(hours=12)).replace(second=0, microsecond=0)
    start_sgt = start_utc + timedelta(hours=8)
    resp = request_sgt(client, v, e1, start_sgt, start_sgt + timedelta(hours=2))
    assert resp.status_code == 201, resp.text
    login(client, "coord_a")
    [booking] = client.get(f"/events/{e1.id}/bookings").json()
    assert booking["status"] == "Pending"
    assert datetime.fromisoformat(booking["hold_expires_at"]) == start_utc
    assert datetime.fromisoformat(booking["booking_start"]) == start_utc


def test_tc9516_venue_staff_cannot_extend_past_the_real_booking_start(client, clock, v, e1):
    """TC-9516 (SCRUM-95 AC1, D3, Negative Path): D 10:00 at +08:00 starts at
    D 02:00 UTC, so an expiry of D 05:00 UTC is after the start (422)."""
    booking_id = request_sgt(client, v, e1, d_at(clock, 10), d_at(clock, 12)).json()["booking"]["booking_id"]
    late = set_expiry(client, booking_id, d_at(clock, 5).isoformat() + "Z")
    assert late.status_code == 422
    assert late.json()["detail"] == f"The new expiry can't be later than the booking's start ({fmt(d_at(clock, 10))})"
    assert set_expiry(client, booking_id, d_at(clock, 1).isoformat() + "Z").status_code == 200


def test_tc9517_messages_write_booking_times_in_singapore_time(client, clock, db_session, v, e1, e2):
    """TC-9517 (SCRUM-95 AC1, AC3, Cross-Cutting): messages write the booked
    period in Singapore time (team assumption), in the same words as before, so
    it matches what the Coordinator entered. The hold expiry stays UTC."""
    booking_id = request_sgt(client, v, e1, d_at(clock, 10), d_at(clock, 12)).json()["booking"]["booking_id"]
    period = f"({fmt(d_at(clock, 10))} - 12:00)"

    clash = request_sgt(client, v, e2, d_at(clock, 11), d_at(clock, 13), coordinator="coord_b")
    assert clash.status_code == 409
    assert clash.json()["detail"] == f"Direct conflict with existing booking {period}"

    assert set_expiry(client, booking_id, d_at(clock, 1).isoformat() + "Z").status_code == 200
    [expiry_set] = notices(client, "coord_a", "hold_expiry_set")
    assert period in expiry_set["message"]
    assert expiry_set["message"].endswith(f"now expires at {fmt(d_at(clock, 1))} UTC.")

    with TestingSessionLocal() as fresh:
        submitted = fresh.query(Notification).filter_by(booking_id=booking_id, kind="booking_submitted").first()
        assert submitted is not None
        assert f"from {d_at(clock, 10).isoformat(sep=' ')} to {d_at(clock, 12).isoformat(sep=' ')} was submitted" in submitted.message


def test_tc9518_venue_search_converts_offsets_to_utc(client, clock, v, e1):
    """TC-9518 (SCRUM-95 AC1, D7, Workflow Variation): a search at D 10:30-11:00
    +08:00 overlaps the booking stored at D 02:00-04:00 UTC."""
    assert request_sgt(client, v, e1, d_at(clock, 10), d_at(clock, 12)).status_code == 201
    login(client, "coord_a")
    resp = client.get("/venues/search", params={"start_time": sgt(d_at(clock, 10, 30)), "end_time": sgt(d_at(clock, 11))})
    assert resp.status_code == 200, resp.text
    [hall] = [venue for venue in resp.json() if venue["id"] == v.id]
    assert hall["is_available"] is False
