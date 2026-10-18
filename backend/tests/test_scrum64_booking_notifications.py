from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from backend.models import Notification, User, LoginSession, VenueBooking
from backend.notifications import notify_booking, KIND_BOOKING_REJECTED
from backend.tests.test_scrum37_rejection_reason import db_session, client, seed_data


def as_user(client, user):
    client.cookies.set("connectsphere_session", f"scrum64_{user.id}")


def setup_users(db_session, seed_data):
    other = User(username="other_staff", password_hash="unused", role=" VENUE_STAFF ")
    lead = User(username="lead64", password_hash="unused", role="lead")
    db_session.add_all([other, lead])
    db_session.flush()
    for user in [*filter(lambda x: isinstance(x, User), seed_data.values()), other, lead]:
        db_session.add(LoginSession(user_id=user.id, token=f"scrum64_{user.id}", valid=True))
    db_session.commit()
    return other, lead


def submit(client, data):
    start = datetime.utcnow().replace(microsecond=0) + timedelta(days=100)
    return client.post(f"/venues/{data['venue'].id}/bookings", json={
        "event_id": data['event'].id, "booking_start": start.isoformat(),
        "booking_end": (start + timedelta(hours=2)).isoformat()})


@pytest.mark.parametrize("outcome", ["approve", "reject"])
def test_booking_lifecycle(client, db_session, seed_data, outcome):
    other, lead = setup_users(db_session, seed_data)
    coord = seed_data['coord']
    as_user(client, coord)
    response = submit(client, seed_data)
    assert response.status_code == 201
    booking = db_session.get(VenueBooking, response.json()['booking']['id'])
    assert booking.requester_id == coord.id
    rows = db_session.query(Notification).all()
    assert {n.user_id for n in rows} == {seed_data['staff'].id, other.id}
    assert submit(client, seed_data).status_code == 409
    seed_data['event'].assigned_coordinator_id = lead.id
    db_session.commit()
    as_user(client, seed_data['staff'])
    reason = "Persisted reason " + "long explanation " * 100
    response = client.post(f"/venues/bookings/{booking.id}/{outcome}",
                           json={"reason": f"  {reason.strip()}  "})
    assert response.status_code == 200
    assert client.post(f"/venues/bookings/{booking.id}/{outcome}", json={"reason": "again"}).status_code == 409
    assert db_session.query(Notification).count() == 3
    as_user(client, coord)
    notifications = client.get('/notifications').json()
    assert len(notifications) == 1
    message = notifications[0]['message']
    assert seed_data['venue'].name in message
    assert booking.booking_start.isoformat(sep=' ') in message
    assert booking.booking_end.isoformat(sep=' ') in message
    if outcome == 'reject':
        assert booking.decision.reason == reason.strip()
        assert reason.strip() in message
        notify_booking(db_session, booking, KIND_BOOKING_REJECTED)
        db_session.commit()
        assert db_session.query(Notification).count() == 3
    as_user(client, other)
    assert len(client.get('/notifications').json()) == 1
    assert client.post(f"/notifications/{notifications[0]['id']}/read").status_code == 404
    as_user(client, lead)
    assert client.get('/notifications').json() == []
    # Database uniqueness, independently of helper checks.
    n = db_session.query(Notification).first()
    db_session.add(Notification(user_id=n.user_id, booking_id=n.booking_id, kind=n.kind, message='duplicate'))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_lead_and_failed_operations(client, db_session, seed_data, monkeypatch):
    _, lead = setup_users(db_session, seed_data)
    as_user(client, seed_data['organiser'])
    assert submit(client, seed_data).status_code == 403
    as_user(client, seed_data['staff'])
    assert client.post(f"/venues/bookings/{seed_data['booking'].id}/reject", json={"reason": " "}).status_code == 422
    assert db_session.query(Notification).count() == 0
    as_user(client, lead)
    original_commit = db_session.commit
    def fail_commit():
        raise RuntimeError('simulated transaction failure')
    monkeypatch.setattr(db_session, 'commit', fail_commit)
    with pytest.raises(RuntimeError, match='simulated'):
        submit(client, seed_data)
    db_session.rollback()
    assert db_session.query(Notification).count() == 0
    assert db_session.query(VenueBooking).count() == 1
    monkeypatch.setattr(db_session, 'commit', original_commit)
    response = submit(client, seed_data)
    assert response.status_code == 201
    assert db_session.get(VenueBooking, response.json()['booking']['id']).requester_id == lead.id


def test_historical_decision(client, db_session, seed_data):
    setup_users(db_session, seed_data)
    as_user(client, seed_data['staff'])
    assert seed_data['booking'].requester_id is None
    assert client.post(f"/venues/bookings/{seed_data['booking'].id}/reject", json={"reason": "recorded"}).status_code == 200
    assert db_session.query(Notification).count() == 0


def test_schema_initialization_preserves_historical_records_and_is_idempotent(db_session, seed_data):
    from backend.database import Base
    booking = seed_data['booking']
    notification = Notification(user_id=seed_data['coord'].id, kind='submitted', message='Existing message')
    db_session.add(notification)
    db_session.commit()
    engine = db_session.get_bind()
    Base.metadata.create_all(engine)
    Base.metadata.create_all(engine)
    db_session.expire_all()
    assert booking.requester_id is None
    assert notification.booking_id is None
    assert notification.message == 'Existing message'
    db_session.add(Notification(user_id=seed_data['coord'].id, booking_id=booking.id,
                                kind='booking_submitted', message='First'))
    db_session.commit()
    db_session.add(Notification(user_id=seed_data['coord'].id, booking_id=booking.id,
                                kind='booking_submitted', message='Duplicate'))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    assert any(key['constrained_columns'] == ['requester_id'] and key['referred_table'] == 'users'
               for key in inspect(engine).get_foreign_keys('venue_bookings'))


def test_fallback_reason_and_unrelated_notification_permissions(client, db_session, seed_data):
    from backend.notifications import notify, NotEntitled, can_see_event
    setup_users(db_session, seed_data)
    booking = seed_data['booking']
    booking.requester_id = seed_data['coord'].id
    booking.rejection_reason = 'Previously recorded alternative'
    notify_booking(db_session, booking, KIND_BOOKING_REJECTED)
    db_session.commit()
    as_user(client, seed_data['coord'])
    assert 'Previously recorded alternative' in client.get('/notifications').json()[0]['message']
    assert not can_see_event(seed_data['staff'], seed_data['event'])
    with pytest.raises(NotEntitled):
        notify(db_session, seed_data['staff'], seed_data['event'], 'submitted', 'unrelated')


def test_decision_transaction_failure_rolls_back(client, db_session, seed_data, monkeypatch):
    setup_users(db_session, seed_data)
    booking = seed_data['booking']
    booking.requester_id = seed_data['coord'].id
    db_session.commit()
    as_user(client, seed_data['staff'])
    def fail_commit():
        raise RuntimeError('failed decision commit')
    monkeypatch.setattr(db_session, 'commit', fail_commit)
    with pytest.raises(RuntimeError, match='failed decision'):
        client.post(f'/venues/bookings/{booking.id}/approve')
    db_session.rollback()
    assert booking.status == 'Pending'
    assert booking.decision is None
    assert db_session.query(Notification).count() == 0
