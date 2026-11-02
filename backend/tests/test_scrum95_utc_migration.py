"""SCRUM-95 one-off fix tests use disposable SQLite, never the configured Neon connection."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import User, Event, Venue, VenueBooking, VenueHold
from backend.migrate_scrum95 import check, migrate, MigrationError

CUTOFF = datetime(2026, 11, 3, 2, 0)
OLD_START = datetime(2026, 12, 1, 10, 0)  # Singapore wall time, as stored before SCRUM-95
NEW_START = datetime(2026, 12, 1, 2, 0)   # stored in UTC after SCRUM-95


def database():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert(), [
            {'id': 1, 'username': 'coordinator', 'password_hash': 'unused', 'role': 'coordinator'},
            {'id': 2, 'username': 'staff', 'password_hash': 'unused', 'role': 'venue_staff'}])
        connection.execute(Event.__table__.insert(), {'id': 1, 'title': 'E', 'organiser_id': 1, 'status': 'Approved'})
        connection.execute(Venue.__table__.insert(), {'id': 1, 'name': 'Hall', 'location': 'Campus', 'capacity': 100})
        connection.execute(VenueBooking.__table__.insert(), [
            {'id': 1, 'event_id': 1, 'venue_id': 1, 'status': 'Pending', 'created_at': CUTOFF - timedelta(days=1),
             'booking_start': OLD_START, 'booking_end': OLD_START + timedelta(hours=2)},
            {'id': 2, 'event_id': 1, 'venue_id': 1, 'status': 'Pending', 'created_at': CUTOFF - timedelta(days=1),
             'booking_start': OLD_START, 'booking_end': OLD_START + timedelta(hours=2)},
            {'id': 3, 'event_id': 1, 'venue_id': 1, 'status': 'Pending', 'created_at': CUTOFF + timedelta(hours=1),
             'booking_start': NEW_START, 'booking_end': NEW_START + timedelta(hours=2)}])
        connection.execute(VenueHold.__table__.insert(), [
            # Default expiry capped at the old start: pulled back to the real start.
            {'id': 1, 'booking_id': 1, 'expires_at': OLD_START, 'expires_set_by_id': None},
            # Set by Venue Staff before the real start: kept.
            {'id': 2, 'booking_id': 2, 'expires_at': NEW_START - timedelta(hours=1), 'expires_set_by_id': 2}])
    return engine


def bookings(engine):
    with engine.connect() as connection:
        return {row.id: (row.booking_start, row.booking_end) for row in
                connection.execute(VenueBooking.__table__.select()).all()}


def expiries(engine):
    with engine.connect() as connection:
        return {row.booking_id: row.expires_at for row in connection.execute(VenueHold.__table__.select()).all()}


def test_tc9524_check_counts_legacy_bookings_and_writes_nothing():
    engine = database()
    before = bookings(engine)
    assert check(engine, CUTOFF) == 2
    assert bookings(engine) == before


def test_tc9524_apply_moves_legacy_bookings_to_utc_once():
    engine = database()
    assert migrate(engine, CUTOFF) == 2
    after = bookings(engine)
    assert after[1] == (NEW_START, NEW_START + timedelta(hours=2))
    assert after[2] == (NEW_START, NEW_START + timedelta(hours=2))
    assert after[3] == (NEW_START, NEW_START + timedelta(hours=2))  # already UTC: untouched
    assert expiries(engine) == {1: NEW_START, 2: NEW_START - timedelta(hours=1)}

    with pytest.raises(MigrationError):
        migrate(engine, CUTOFF)
    with pytest.raises(MigrationError):
        check(engine, CUTOFF)
    assert bookings(engine) == after
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM data_migrations")).scalar() == 1
