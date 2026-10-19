"""Migration tests use disposable SQLite, never the configured Neon connection."""
from datetime import datetime, timedelta
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from backend.database import Base, get_db
from backend.main import app
from backend.models import User, Event, Venue, VenueBooking, Notification, LoginSession
from backend.migrate_scrum64 import check, migrate, MigrationError


def database(*, legacy=False, unique=True, references=True):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            sql = str(CreateTable(table).compile(dialect=engine.dialect))
            lines = sql.splitlines()
            if table.name in ('venue_bookings', 'notifications'):
                column = 'requester_id' if table.name == 'venue_bookings' else 'booking_id'
                if legacy:
                    lines = [line for line in lines if not line.strip().startswith(column+' ')
                             and f'FOREIGN KEY({column})' not in line]
                elif not references:
                    lines = [line for line in lines if f'FOREIGN KEY({column})' not in line]
            if table.name == 'notifications' and (legacy or not unique):
                lines = [line for line in lines if 'uq_booking_notification' not in line]
            connection.execute(text(re.sub(r',\s*\)', '\n)', '\n'.join(lines))))
    return engine


def seed(engine):
    start = datetime.utcnow()+timedelta(days=30)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert(), [
            {'id':1,'username':'coordinator','password_hash':'unused','role':'coordinator'},
            {'id':2,'username':'staff','password_hash':'unused','role':'venue_staff'},
            {'id':3,'username':'organiser','password_hash':'unused','role':'organiser'}])
        connection.execute(Event.__table__.insert(), {'id':1,'title':'Preserve me','organiser_id':3,'assigned_coordinator_id':1,'status':'Submitted'})
        connection.execute(Venue.__table__.insert(), {'id':1,'name':'Legacy Hall','location':'Campus','capacity':100})
        connection.execute(VenueBooking.__table__.insert(), {'id':1,'event_id':1,'venue_id':1,'booking_start':start,'booking_end':start+timedelta(hours=1),'status':'Pending'})
        connection.execute(Notification.__table__.insert(), [
            {'id':1,'user_id':1,'event_id':1,'kind':'submitted','message':'Keep original event notice'},
            {'id':2,'user_id':1,'event_id':1,'kind':'submitted','message':'Keep repeated event notice'}])
        connection.execute(LoginSession.__table__.insert(), [
            {'user_id':1,'token':'migration-coordinator','valid':True},
            {'user_id':2,'token':'migration-staff','valid':True}])


def snapshot(engine, columns=None):
    with engine.connect() as connection:
        names = columns or {table: [c['name'] for c in inspect(connection).get_columns(table)]
                            for table in ('users','events','venues','venue_bookings','notifications')}
        rows = {table: connection.execute(text(f"SELECT {','.join(cols)} FROM {table} ORDER BY id")).all()
                for table,cols in names.items()}
    return names, rows


def test_legacy_upgrade_preserves_rows_adds_nullable_foreign_keys_and_is_repeatable():
    engine=database(legacy=True); seed(engine)
    columns,before=snapshot(engine)
    assert len(check(engine))==3
    assert len(migrate(engine))==3
    assert snapshot(engine,columns)[1]==before
    assert check(engine)==[] and migrate(engine)==[]
    with Session(engine) as db:
        assert db.get(VenueBooking,1).requester_id is None
        assert db.get(Notification,1).booking_id is None
    for table,column,target in [('venue_bookings','requester_id','users'),('notifications','booking_id','venue_bookings')]:
        assert any(fk['constrained_columns']==[column] and fk['referred_table']==target for fk in inspect(engine).get_foreign_keys(table))


def test_upgraded_database_can_submit_decide_and_deliver_booking_notifications():
    engine=database(legacy=True); seed(engine); migrate(engine)
    def get_test_db():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db]=get_test_db
    try:
        with TestClient(app) as client:
            client.cookies.set('connectsphere_session','migration-coordinator')
            start=datetime.utcnow()+timedelta(days=100)
            response=client.post('/venues/1/bookings',json={'event_id':1,'booking_start':start.isoformat(),'booking_end':(start+timedelta(hours=1)).isoformat()})
            assert response.status_code==201
            booking_id=response.json()['booking']['id']
            client.cookies.set('connectsphere_session','migration-staff')
            row=client.get('/notifications').json()[0]
            assert row['kind']=='booking_submitted' and row['booking_id']==booking_id
            assert client.post(f'/venues/bookings/{booking_id}/reject',json={'reason':'Persisted reason'}).status_code==200
            client.cookies.set('connectsphere_session','migration-coordinator')
            row=client.get('/notifications').json()[0]
            assert row['kind']=='booking_rejected' and 'Persisted reason' in row['message']
        with Session(engine) as db:
            assert db.get(VenueBooking,booking_id).requester_id==1
            assert db.get(Notification,1).message=='Keep original event notice'
    finally:
        app.dependency_overrides.pop(get_db,None)


def test_fresh_database_needs_no_upgrade_and_equivalent_index_name_is_reused():
    engine=database(unique=False)
    with engine.begin() as connection:
        connection.execute(text('CREATE UNIQUE INDEX existing_neon_name ON notifications (user_id,booking_id,kind)'))
    assert check(engine)==[] and migrate(engine)==[]
    assert [index['name'] for index in inspect(engine).get_indexes('notifications')]==['existing_neon_name']
    fresh=create_engine('sqlite:///:memory:'); Base.metadata.create_all(fresh)
    assert check(fresh)==[] and migrate(fresh)==[]


def test_duplicate_booking_notifications_abort_without_deleting_rows():
    engine=database(unique=False); seed(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE notifications SET booking_id=1 WHERE id IN (1,2)"))
    before=snapshot(engine)[1]
    with pytest.raises(MigrationError,match='Duplicate booking notifications'):
        migrate(engine)
    assert snapshot(engine)[1]==before
    assert inspect(engine).get_indexes('notifications')==[]


def test_missing_index_deduplicates_bookings_but_preserves_null_event_links():
    engine=database(unique=False); seed(engine)
    assert migrate(engine)==['Add booking notification uniqueness']
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO notifications (id,user_id,booking_id,kind,message,created_at) VALUES (3,1,1,'booking_submitted','first',CURRENT_TIMESTAMP)"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO notifications (id,user_id,booking_id,kind,message,created_at) VALUES (4,1,1,'booking_submitted','duplicate',CURRENT_TIMESTAMP)"))
    assert len(snapshot(engine)[1]['notifications'])==3


def test_orphan_reference_is_reported_without_data_repair():
    engine=database(unique=False); seed(engine)
    with engine.begin() as connection:
        connection.execute(text('UPDATE venue_bookings SET requester_id=999 WHERE id=1'))
    before=snapshot(engine)[1]
    with pytest.raises(MigrationError,match='Orphan references'):
        migrate(engine)
    assert snapshot(engine)[1]==before


def test_wrong_existing_column_and_missing_sqlite_fk_abort_before_ddl():
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        c.execute(text('CREATE TABLE venue_bookings (id INTEGER PRIMARY KEY, requester_id TEXT)'))
        c.execute(text('CREATE TABLE notifications (id INTEGER PRIMARY KEY,user_id INTEGER,kind TEXT)'))
    with pytest.raises(MigrationError,match='nullable integer'):
        migrate(engine)
    assert 'booking_id' not in {c['name'] for c in inspect(engine).get_columns('notifications')}
    engine=database(references=False)
    with pytest.raises(MigrationError,match='Missing foreign key'):
        migrate(engine)


def test_check_is_read_only_and_conflicting_index_name_is_not_silently_accepted():
    engine=database(legacy=True); seed(engine)
    before=snapshot(engine)
    assert len(check(engine))==3
    assert snapshot(engine)==before
    engine=database(unique=False)
    with engine.begin() as c:
        c.execute(text('CREATE INDEX uq_booking_notification ON notifications (booking_id,kind,user_id)'))
    with pytest.raises(MigrationError,match='not a compatible full unique index'):
        migrate(engine)


def test_schema_ddl_failure_rolls_back_all_added_columns(monkeypatch):
    from sqlalchemy import event
    engine=database(legacy=True); seed(engine)
    before=snapshot(engine)
    def fail_index(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('CREATE UNIQUE INDEX'):
            raise RuntimeError('injected DDL failure')
    event.listen(engine,'before_cursor_execute',fail_index)
    try:
        with pytest.raises(RuntimeError,match='injected DDL'):
            migrate(engine)
    finally:
        event.remove(engine,'before_cursor_execute',fail_index)
    assert snapshot(engine)==before
    assert 'requester_id' not in {c['name'] for c in inspect(engine).get_columns('venue_bookings')}
    assert 'booking_id' not in {c['name'] for c in inspect(engine).get_columns('notifications')}


def test_partial_upgrade_adds_only_the_missing_requester_reference():
    engine=database(legacy=True); seed(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE notifications ADD COLUMN booking_id INTEGER REFERENCES venue_bookings(id)'))
        connection.execute(text('CREATE UNIQUE INDEX existing_neon_name ON notifications (user_id,booking_id,kind)'))
    columns,before=snapshot(engine)
    assert migrate(engine)==['Add nullable venue_bookings.requester_id with foreign key']
    assert snapshot(engine,columns)[1]==before
    assert check(engine)==[]
    assert [i['name'] for i in inspect(engine).get_indexes('notifications')]==['existing_neon_name']
