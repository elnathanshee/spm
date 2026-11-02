"""One-off SCRUM-95 data fix: move bookings saved before D7 from Singapore time to UTC.

Before SCRUM-95, booking_start/booking_end were stored as the Singapore wall
time the user typed. They are now stored in UTC, so those older rows display
and clash-check 8 hours late. This subtracts 8 hours from every booking created
before the given cutoff (the SCRUM-95 deploy time, in UTC), and pulls any hold
expiry that is now after the real start back to it (SCRUM-81 D2/D3 cap).

--check is read-only. --apply runs once: it records itself in data_migrations
and refuses a second run, so no row is shifted twice.

Never imports backend.main or initializes/recreates tables.
"""
import argparse
from datetime import datetime, timedelta

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

NAME = "scrum95_bookings_to_utc"
SINGAPORE_OFFSET = timedelta(hours=8)


class MigrationError(RuntimeError):
    """The fix cannot run safely; no records were changed."""


def _applied(connection):
    if not inspect(connection).has_table("data_migrations"):
        return False
    return connection.execute(text("SELECT 1 FROM data_migrations WHERE name=:name"), {"name": NAME}).first() is not None


def _legacy(connection, cutoff):
    return connection.execute(text("SELECT id, booking_start, booking_end FROM venue_bookings "
        "WHERE created_at < :cutoff ORDER BY id"), {"cutoff": cutoff}).all()


def check(target_engine, cutoff):
    """Return how many bookings would move. Never writes."""
    with target_engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
        if _applied(connection):
            raise MigrationError("Already applied; nothing to do")
        return len(_legacy(connection, cutoff))


def migrate(target_engine, cutoff):
    """Shift legacy bookings by -8h in one transaction. Returns the number moved."""
    if target_engine.dialect.name not in {"postgresql", "sqlite"}:
        raise MigrationError("Only PostgreSQL and SQLite are supported")
    with target_engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS data_migrations "
                                "(name VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"))
        if connection.dialect.name == "postgresql":
            connection.execute(text("LOCK TABLE venue_bookings, venue_holds, data_migrations IN SHARE ROW EXCLUSIVE MODE"))
        if _applied(connection):
            raise MigrationError("Already applied; no rows were shifted again")
        rows = _legacy(connection, cutoff)
        for booking_id, start, end in rows:
            start, end = _as_datetime(start) - SINGAPORE_OFFSET, _as_datetime(end) - SINGAPORE_OFFSET
            connection.execute(text("UPDATE venue_bookings SET booking_start=:start, booking_end=:end WHERE id=:id"),
                               {"start": start, "end": end, "id": booking_id})
            # Expiries were capped at the old (8h late) start; keep them at or before the real start.
            connection.execute(text("UPDATE venue_holds SET expires_at=:start WHERE booking_id=:id "
                                    "AND expires_at > :start"),
                               {"start": start, "id": booking_id})
        connection.execute(text("INSERT INTO data_migrations (name, applied_at) VALUES (:name, :at)"),
                           {"name": NAME, "at": datetime.utcnow()})
        return len(rows)


def _as_datetime(value):
    # SQLite text() queries return strings; PostgreSQL returns datetimes.
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only count of bookings that would move")
    mode.add_argument("--apply", action="store_true", help="Apply once, after a verified backup")
    parser.add_argument("--created-before", required=True, type=datetime.fromisoformat,
                        help="UTC deploy time of SCRUM-95, e.g. 2026-11-03T02:00; earlier bookings are shifted")
    args = parser.parse_args()
    if args.created_before.tzinfo is not None:
        parser.error("--created-before is UTC; give it without an offset")
    from .database import engine
    print(f"Database target: {engine.url.host or 'local'} / {engine.url.database}")
    try:
        count = check(engine, args.created_before) if args.check else migrate(engine, args.created_before)
    except MigrationError as error:
        print(f"SCRUM-95 fix not run: {error}")
        return 1
    except SQLAlchemyError as error:
        print(f"Database operation failed ({type(error).__name__}); no credentials printed. Review database diagnostics locally.")
        return 1
    print(f"{count} booking(s) {'would move' if args.check else 'moved'} 8 hours earlier (Singapore time to UTC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
