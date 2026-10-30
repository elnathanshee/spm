"""GitHub #29: revalidate SCRUM-80 conflict flags once a hold expires.

When a tentative hold expires (SCRUM-81) it stops holding the venue, but the
SCRUM-80 conflict flags raised against it stayed on, so Venue Staff saw
outdated "Possible clash" warnings.

Expected behaviour (from #29), each test names the point it proves:
  E1  Expired holds no longer cause active conflict flags.
  E2  Existing flags are revalidated after hold expiry.
  E3  Other valid conflicts stay flagged.
  E4  Venue availability and booking decisions are unaffected by stale flags.
  E5  Historical conflict information is preserved.

The fix revalidates at read time: a stored flag is shown only while a real
clash still exists. The flag rows are never removed (E5), so no table changes.

Times: Grand Hall, 30 min setup and 45 min turnaround. On day D:
  A  Pending  10:00-12:00  hold +3 days
  B  Pending  13:30-15:00  hold +3 days  (15 min gap after A)
Raising the turnaround to 75 makes A and B clash, so both are flagged.
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import (
    Event,
    LoginSession,
    User,
    Venue,
    VenueBooking,
    VenueBookingConflictFlag,
    VenueHold,
)
from backend.security import hash_password

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seed_data(db_session, client):
    staff = User(username="staff_alex", password_hash=hash_password("password123"), role="venue_staff")
    coord = User(username="coord_sarah", password_hash=hash_password("password123"),
                 role="coordinator", is_active_coordinator=True)
    organiser = User(username="org_dave", password_hash=hash_password("password123"), role="organiser")
    db_session.add_all([staff, coord, organiser])
    db_session.commit()
    db_session.add(LoginSession(user_id=staff.id, token="token_staff_alex", valid=True))

    hall = Venue(name="Grand Hall", location="Building A", capacity=200,
                 facilities=json.dumps(["Projector"]), supported_layouts=json.dumps(["Theatre"]),
                 operating_hours="08:00-22:00", setup_time_mins=30, turnaround_time_mins=45, is_active=True)
    db_session.add(hall)
    db_session.commit()

    event = Event(title="Annual Tech Symposium", organiser_id=organiser.id, status="Approved",
                  proposed_start=datetime.utcnow() + timedelta(days=30), assigned_coordinator_id=coord.id)
    db_session.add(event)
    db_session.commit()

    day = (datetime.utcnow() + timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

    def at(h, m=0):
        return day + timedelta(hours=h, minutes=m)

    a = VenueBooking(event_id=event.id, venue_id=hall.id, requester_id=coord.id,
                     booking_start=at(10), booking_end=at(12), status="Pending")
    b = VenueBooking(event_id=event.id, venue_id=hall.id, requester_id=coord.id,
                     booking_start=at(13, 30), booking_end=at(15), status="Pending")
    db_session.add_all([a, b])
    db_session.commit()
    future = datetime.utcnow() + timedelta(days=3)
    db_session.add_all([VenueHold(booking_id=a.id, expires_at=future), VenueHold(booking_id=b.id, expires_at=future)])
    db_session.commit()

    # Raise the turnaround so A and B clash: SCRUM-80 flags both.
    client.cookies.set("connectsphere_session", "token_staff_alex")
    res = client.patch(f"/venues/{hall.id}", json={"turnaround_time_mins": 75})
    assert res.status_code == 200
    assert sorted(f["booking_id"] for f in res.json()["flagged_bookings"]) == sorted([a.id, b.id])

    return {"hall": hall, "event": event, "a": a, "b": b, "at": at}


def _expire_hold(db_session, booking):
    booking.hold.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()


def _review(client, booking):
    return client.get(f"/venues/bookings/{booking.id}").json()


def _listed(client, hall):
    return {row["booking_id"]: row for row in client.get(f"/venues/{hall.id}/bookings").json()}


def test_tc8013_expired_hold_no_longer_causes_an_active_flag(client, db_session, seed_data):
    """TC-8013 (#29 E1, E2): once B's hold expires, A's flag against B is no
    longer active, in the review and in the venue's booking list. B, now
    Expired, has no active flag either."""
    a, b, hall = seed_data["a"], seed_data["b"], seed_data["hall"]
    assert _review(client, a)["conflict_flag"]["conflicting_booking_id"] == b.id  # flagged before expiry

    _expire_hold(db_session, b)

    assert _review(client, a)["conflict_flag"] is None
    listed = _listed(client, hall)
    assert listed[a.id]["conflict_flag"] is None
    assert listed[b.id]["status"] == "Expired"
    assert listed[b.id]["conflict_flag"] is None


def test_tc8014_another_real_clash_stays_flagged(client, db_session, seed_data):
    """TC-8014 (#29 E3): A's stored flag names B. If A also clashes with
    another booking C, A stays flagged after B expires, and the flag now names
    C, the clash that still exists."""
    a, b, hall, at = seed_data["a"], seed_data["b"], seed_data["hall"], seed_data["at"]
    c = VenueBooking(event_id=seed_data["event"].id, venue_id=hall.id,
                     booking_start=at(7), booking_end=at(9), status="Confirmed")  # 9:00 + 75 min turnaround overlaps A's 9:30 setup
    db_session.add(c)
    db_session.commit()

    _expire_hold(db_session, b)

    flag = _review(client, a)["conflict_flag"]
    assert flag is not None
    assert flag["conflicting_booking_id"] == c.id
    assert f"#{c.id}" in flag["reason"] and "75m turnaround" in flag["reason"]


def test_tc8015_flag_history_is_preserved(client, db_session, seed_data):
    """TC-8015 (#29 E5): revalidation never deletes or rewrites the stored
    flags; the record of the original clash is kept for auditing."""
    a, b = seed_data["a"], seed_data["b"]
    _expire_hold(db_session, b)
    _review(client, a)
    _listed(client, seed_data["hall"])

    db_session.expire_all()
    stored = {f.booking_id: f for f in db_session.query(VenueBookingConflictFlag).all()}
    assert set(stored) == {a.id, b.id}
    assert stored[a.id].conflicting_booking_id == b.id
    assert "75m turnaround" in stored[a.id].reason


def test_tc8016_decisions_are_unaffected_by_stale_flags(client, db_session, seed_data):
    """TC-8016 (#29 E4): while B holds the venue, approving A is refused (409);
    once B's hold expires, the stale flag doesn't stop A being approved."""
    a, b = seed_data["a"], seed_data["b"]

    res = client.post(f"/venues/bookings/{a.id}/approve")
    assert res.status_code == 409
    assert "Cannot approve" in res.json()["detail"]

    _expire_hold(db_session, b)

    res = client.post(f"/venues/bookings/{a.id}/approve")
    assert res.status_code == 200
    assert res.json()["booking"]["status"] == "Confirmed"
    assert res.json()["booking"]["conflict_flag"] is None


def test_tc8017_rejected_partner_also_clears_the_flag(client, db_session, seed_data):
    """TC-8017 (#29 E1, Boundary): the same rule covers any booking that stops
    holding the venue. If B is rejected instead of expiring, A's flag is no
    longer active."""
    a, b = seed_data["a"], seed_data["b"]
    res = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Turnaround now clashes with A"})
    assert res.status_code == 200

    assert _review(client, a)["conflict_flag"] is None
    assert _review(client, b)["conflict_flag"] is None


# ---------------------------------------------------------------------------
# PR #35 review (Darryl, 28 Oct)
# ---------------------------------------------------------------------------

def _count_booking_queries(fn):
    """Run fn and count the SQL statements that read the venue_bookings table
    (FROM venue_bookings, not the venue_booking_* tables)."""
    import re
    from sqlalchemy import event

    pattern = re.compile(r"\bFROM venue_bookings\b", re.IGNORECASE)
    count = {"n": 0}

    def before(conn, cursor, statement, parameters, context, executemany):
        if pattern.search(statement):
            count["n"] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return count["n"]


def _add_flagged_pair(db_session, seed_data, day_offset):
    """A Pending booking P (10:00-12:00) flagged against a Confirmed booking Q
    (13:00-14:00) on another day: with 30 min setup and 75 min turnaround they
    really clash, so P's flag stays active."""
    hall, event = seed_data["hall"], seed_data["event"]
    day = seed_data["at"](0) + timedelta(days=day_offset)
    p = VenueBooking(event_id=event.id, venue_id=hall.id, booking_start=day + timedelta(hours=10),
                     booking_end=day + timedelta(hours=12), status="Pending")
    q = VenueBooking(event_id=event.id, venue_id=hall.id, booking_start=day + timedelta(hours=13),
                     booking_end=day + timedelta(hours=14), status="Confirmed")
    db_session.add_all([p, q])
    db_session.commit()
    db_session.add(VenueBookingConflictFlag(booking_id=p.id, conflicting_booking_id=q.id, reason="seeded"))
    db_session.commit()
    return p


def test_tc8018_listing_does_not_requery_the_venue_per_flagged_booking(client, db_session, seed_data):
    """TC-8018 (PR #35 review, Medium): revalidating flags loads the venue's
    bookings once per list, so the number of venue_bookings queries doesn't
    grow with the number of flagged bookings."""
    hall = seed_data["hall"]

    def list_bookings():
        rows = client.get(f"/venues/{hall.id}/bookings").json()
        list_bookings.active = sum(1 for row in rows if row["conflict_flag"])

    with_two = _count_booking_queries(list_bookings)
    assert list_bookings.active == 2  # A and B

    for offset in (1, 2, 3, 4):
        _add_flagged_pair(db_session, seed_data, offset)
    db_session.expire_all()

    with_six = _count_booking_queries(list_bookings)
    assert list_bookings.active == 6  # the flags are real, so they are all revalidated
    assert with_six == with_two


def test_tc8019_active_reason_uses_the_current_buffers(client, db_session, seed_data):
    """TC-8019 (PR #35 review, Low): when the named clash still exists, the
    shown reason uses the venue's current setup and turnaround; the stored
    flag keeps its original reason as history."""
    a, b, hall = seed_data["a"], seed_data["b"], seed_data["hall"]
    res = client.patch(f"/venues/{hall.id}", json={"turnaround_time_mins": 90})
    assert res.status_code == 200

    flag = _review(client, a)["conflict_flag"]
    assert flag["conflicting_booking_id"] == b.id
    assert "90m turnaround" in flag["reason"]
    assert "75m" not in flag["reason"]

    db_session.expire_all()
    stored = db_session.query(VenueBookingConflictFlag).filter_by(booking_id=a.id).one()
    assert "75m turnaround" in stored.reason
