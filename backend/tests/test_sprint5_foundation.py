"""[Sprint 5] The shared helpers that several Sprint 5 stories build on.

SCRUM-102's gate reads equipment status (SCRUM-40). Cancelling an event must
release equipment reservations (SCRUM-44) and tell registered Attendees
(SCRUM-52). Safety decisions (SCRUM-87) and approved changes (SCRUM-50) mark
arrangements for review. These tests pin the shared contract those stories use.
"""
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import arrangements, event_hooks
from backend.database import Base
from backend.main import app  # noqa: F401  (registers every table)
from backend.models import Event, EquipmentRequest, User, Venue, VenueBooking

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def world(db):
    coord = User(username="coord1", password_hash="x", role="coordinator", is_active_coordinator=True)
    organiser = User(username="organiser1", password_hash="x", role="organiser")
    db.add_all([coord, organiser])
    db.flush()
    event = Event(title="Annual Gala", organiser_id=organiser.id, status="Planning",
                  assigned_coordinator_id=coord.id, proposed_start=datetime(2099, 3, 10, 14, 0))
    hall = Venue(name="Grand Hall", location="Block A", capacity=100, setup_time_mins=30, turnaround_time_mins=45)
    db.add_all([event, hall])
    db.flush()
    return {"coord": coord, "event": event, "hall": hall}


def _request(db, world, kind="Projector"):
    request = EquipmentRequest(event_id=world["event"].id, equipment_type=kind, quantity=2, requested_by_id=world["coord"].id)
    db.add(request)
    db.flush()
    return request


def _booking(db, world, start, end, status="Confirmed"):
    booking = VenueBooking(event_id=world["event"].id, venue_id=world["hall"].id, booking_start=start, booking_end=end, status=status)
    db.add(booking)
    db.flush()
    return booking


def test_tc10290_equipment_status_defaults_to_requested_and_keeps_history(db, world):
    """TC-10290: a request nobody has updated reads 'requested'; the latest update wins; history is kept."""
    request = _request(db, world)
    assert arrangements.latest_equipment_status(db, request) == "requested"
    arrangements.record_equipment_status(db, request, "reserved", "Two held", world["coord"])
    arrangements.record_equipment_status(db, request, "arranged", None, world["coord"])
    db.flush()
    assert arrangements.latest_equipment_status(db, request) == "arranged"
    assert [u.status for u in arrangements.equipment_status_history(db, request)] == ["reserved", "arranged"]


def test_tc10291_unknown_equipment_status_is_refused(db, world):
    """TC-10291: only requested, reserved, arranged and unavailable are statuses."""
    with pytest.raises(ValueError):
        arrangements.record_equipment_status(db, _request(db, world), "lost", None, world["coord"])


def test_tc10292_all_equipment_arranged(db, world):
    """TC-10292: SCRUM-102's gate. No requests counts as arranged; one unarranged request does not."""
    assert arrangements.all_equipment_arranged(db, world["event"]) is True
    first, second = _request(db, world, "Projector"), _request(db, world, "Microphone")
    arrangements.record_equipment_status(db, first, "arranged", None, world["coord"])
    db.flush()
    assert arrangements.all_equipment_arranged(db, world["event"]) is False
    arrangements.record_equipment_status(db, second, "arranged", None, world["coord"])
    db.flush()
    assert arrangements.all_equipment_arranged(db, world["event"]) is True


def test_tc10293_event_window_with_bookings_includes_setup_and_turnaround(db, world):
    """TC-10293: the window spans every booking that holds a venue, with setup before and turnaround after."""
    _booking(db, world, datetime(2099, 3, 10, 6, 0), datetime(2099, 3, 10, 8, 0))
    _booking(db, world, datetime(2099, 3, 10, 10, 0), datetime(2099, 3, 10, 12, 0), status="Pending")
    _booking(db, world, datetime(2099, 3, 12, 10, 0), datetime(2099, 3, 12, 12, 0), status="Rejected")
    db.flush()
    db.refresh(world["event"])
    start, end = arrangements.event_window(world["event"])
    assert start == datetime(2099, 3, 10, 5, 30)
    assert end == datetime(2099, 3, 10, 12, 45)
    assert arrangements.event_end(world["event"]) == datetime(2099, 3, 10, 12, 0)


def test_tc10294_event_window_without_bookings_is_the_rest_of_the_singapore_day(db, world):
    """TC-10294: proposed_start is Singapore wall time; without a booking the window runs to that day's end, in UTC."""
    start, end = arrangements.event_window(world["event"])
    assert start == datetime(2099, 3, 10, 6, 0)
    assert end == datetime(2099, 3, 10, 16, 0)
    assert arrangements.event_end(world["event"]) == datetime(2099, 3, 10, 16, 0)
    world["event"].proposed_start = None
    assert arrangements.event_window(world["event"]) is None
    assert arrangements.event_end(world["event"]) is None


def test_tc10295_flags_mark_each_arrangement_once_and_keep_history_when_cleared(db, world):
    """TC-10295: flagging marks every holding booking and equipment request, not twice, and clearing keeps the row."""
    booking = _booking(db, world, datetime(2099, 3, 10, 6, 0), datetime(2099, 3, 10, 8, 0))
    _booking(db, world, datetime(2099, 3, 11, 6, 0), datetime(2099, 3, 11, 8, 0), status="Rejected")
    request = _request(db, world)
    db.refresh(world["event"])
    created = arrangements.flag_event_arrangements(db, world["event"], "safety_review", "Exit width", world["coord"])
    db.flush()
    assert {(f.booking_id, f.equipment_request_id) for f in created} == {(booking.id, None), (None, request.id)}
    assert arrangements.flag_event_arrangements(db, world["event"], "safety_review", "Again", world["coord"]) == []
    db.flush()
    assert len(arrangements.active_review_flags(db, world["event"])) == 2
    assert arrangements.clear_review_flags(db, world["event"], world["coord"]) == 2
    db.flush()
    assert arrangements.active_review_flags(db, world["event"]) == []
    # Cleared flags are history, so the same arrangement can be flagged again later.
    assert len(arrangements.flag_event_arrangements(db, world["event"], "important_change", "Date moved", world["coord"])) == 2


def test_tc10296_hooks_run_every_registered_handler_in_order(world, db, monkeypatch):
    """TC-10296: a feature registers once; the story that causes the change just runs the hook."""
    seen = []
    monkeypatch.setattr(event_hooks, "_cancelled", [])
    monkeypatch.setattr(event_hooks, "_important_change", [])
    monkeypatch.setattr(event_hooks, "_safety_returned", [])
    event_hooks.on_event_cancelled(lambda db, event, actor, reason: seen.append(("cancelled-1", reason)))
    event_hooks.on_event_cancelled(lambda db, event, actor, reason: seen.append(("cancelled-2", reason)))
    event_hooks.on_important_change(lambda db, event, actor, fields, reason: seen.append(("change", fields)))
    event_hooks.on_safety_returned(lambda db, event, actor, reason: seen.append(("safety", reason)))
    event_hooks.event_cancelled(db, world["event"], world["coord"], "Rained off")
    event_hooks.important_change(db, world["event"], world["coord"], ["proposed_start"], "Moved")
    event_hooks.safety_returned(db, world["event"], world["coord"], "Exit width")
    assert seen == [("cancelled-1", "Rained off"), ("cancelled-2", "Rained off"),
                    ("change", ["proposed_start"]), ("safety", "Exit width")]
