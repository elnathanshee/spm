"""Explicit SCRUM-64 upgrade. --check is read-only; --apply requires operator approval.

Never imports backend.main or initializes/recreates tables. See docs/SCRUM-64-MIGRATION.md.
"""
import argparse

from sqlalchemy import Integer, inspect, text
from sqlalchemy.exc import SQLAlchemyError

KEY = {"booking_id", "kind", "user_id"}
INDEX_NAME = "uq_booking_notification"
REFERENCES = [("venue_bookings", "requester_id", "users"),
              ("notifications", "booking_id", "venue_bookings")]


class MigrationError(RuntimeError):
    """An incompatible schema/data condition needs operator review; no data is repaired."""


def _plan(connection):
    inspector = inspect(connection)
    for table in ("users", "venue_bookings", "notifications"):
        if not inspector.has_table(table):
            raise MigrationError(f"Missing base table {table}; initialize a fresh database with the application models first")
    operations = []
    for table, column, target in REFERENCES:
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        if column not in columns:
            operations.append((f"Add nullable {table}.{column} with foreign key",
                               f"ALTER TABLE {table} ADD COLUMN {column} INTEGER REFERENCES {target}(id)"))
            continue
        existing = columns[column]
        if not isinstance(existing["type"], Integer) or not existing["nullable"] or existing.get("default") is not None:
            raise MigrationError(f"{table}.{column} must be a nullable integer without a default; review existing schema")
        foreign_keys = [fk for fk in inspector.get_foreign_keys(table) if column in fk["constrained_columns"]]
        compatible = [fk for fk in foreign_keys if fk["constrained_columns"] == [column]
                      and fk["referred_table"] == target and fk["referred_columns"] == ["id"]
                      and fk.get("referred_schema") in (None, inspector.default_schema_name)
                      and not fk.get("options", {}).get("ondelete")]
        if foreign_keys and len(compatible) != len(foreign_keys):
            raise MigrationError(f"Unexpected foreign key on {table}.{column}; manual review required")
        orphan = connection.execute(text(f"SELECT 1 FROM {table} s WHERE s.{column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {target} t WHERE t.id=s.{column}) LIMIT 1")).first()
        if orphan:
            raise MigrationError(f"Orphan references in {table}.{column}; no records were changed")
        if not compatible:
            if connection.dialect.name != "postgresql":
                raise MigrationError(f"Missing foreign key on existing {table}.{column}; SQLite requires an explicit repair, not a table rebuild by this script")
            operations.append((f"Add foreign key for {table}.{column}",
                f"ALTER TABLE {table} ADD CONSTRAINT fk_scrum64_{table}_{column} FOREIGN KEY ({column}) REFERENCES {target}(id)"))
        elif connection.dialect.name == "postgresql":
            # A NOT VALID FK does not prove existing records are valid.
            for fk in compatible:
                valid = connection.execute(text("SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid=to_regclass(:table) AND conname=:name"),
                    {"table": table, "name": fk["name"]}).scalar()
                if not valid:
                    quoted = connection.dialect.identifier_preparer.quote(fk["name"])
                    operations.append((f"Validate foreign key on {table}.{column}",
                                       f"ALTER TABLE {table} VALIDATE CONSTRAINT {quoted}"))
    notification_columns = {c["name"] for c in inspector.get_columns("notifications")}
    if not {"kind", "user_id"} <= notification_columns:
        raise MigrationError("notifications is missing existing kind/user_id columns")
    if "booking_id" in notification_columns:
        duplicate = connection.execute(text("SELECT 1 FROM notifications WHERE booking_id IS NOT NULL "
            "AND kind IS NOT NULL AND user_id IS NOT NULL GROUP BY booking_id,kind,user_id "
            "HAVING count(*)>1 LIMIT 1")).first()
        if duplicate:
            raise MigrationError("Duplicate booking notifications exist; operator must resolve them separately; no rows deleted")
    keys = inspector.get_unique_constraints("notifications") + inspector.get_indexes("notifications")
    pg_valid = None
    if connection.dialect.name == "postgresql":
        # pg_get_indexdef works on supported PostgreSQL versions without relying on PG15-only catalog columns.
        pg_valid = {row[0] for row in connection.execute(text("SELECT c.relname FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid='notifications'::regclass "
            "AND i.indisvalid AND i.indisready AND position('NULLS NOT DISTINCT' in pg_get_indexdef(i.indexrelid))=0"))}
    if any(key.get("dialect_options", {}).get("postgresql_nulls_not_distinct") for key in keys
           if set(key.get("column_names") or []) == KEY):
        raise MigrationError("Existing notification key uses NULLS NOT DISTINCT; operator review required")
    equivalent = [key for key in keys if set(key.get("column_names") or []) == KEY
                  and key.get("unique", True)
                  and key.get("dialect_options", {}).get("postgresql_where") is None
                  and key.get("dialect_options", {}).get("sqlite_where") is None
                  and (pg_valid is None or key["name"] in pg_valid)]
    if not equivalent:
        if any(key["name"] == INDEX_NAME for key in keys):
            raise MigrationError(f"{INDEX_NAME} exists but is not a compatible full unique index; operator review required")
        operations.append(("Add booking notification uniqueness", "CREATE UNIQUE INDEX uq_booking_notification "
                           "ON notifications (booking_id, kind, user_id)"))
    return operations


def check(target_engine):
    """Return required operations. Raises on incompatible data/schema; never writes."""
    with target_engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
        return [description for description, _ in _plan(connection)]


def migrate(target_engine):
    """Apply only missing compatible schema in one transaction; never alter records."""
    if target_engine.dialect.name not in {"postgresql", "sqlite"}:
        raise MigrationError("Only PostgreSQL and SQLite are supported")
    with target_engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")  # Include SQLite DDL in rollback.
        else:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            # Operators must stop writers first. Serialize upgrades and protect preflight data checks.
            if not all(inspect(connection).has_table(t) for t in ("users", "venue_bookings", "notifications")):
                raise MigrationError("Base tables missing; initialize a fresh database first")
            connection.execute(text("LOCK TABLE users, venue_bookings, notifications IN SHARE ROW EXCLUSIVE MODE"))
        operations = _plan(connection)  # All compatibility/data checks occur before the first DDL.
        for _, sql in operations:
            connection.execute(text(sql))
        if _plan(connection):
            raise MigrationError("Post-upgrade verification failed; transaction rolled back")
        return [description for description, _ in operations]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only schema/data preflight")
    mode.add_argument("--apply", action="store_true", help="Apply after approval, verified backup and stopped writers")
    args = parser.parse_args()
    from .database import engine
    print(f"Database target: {engine.url.host or 'local'} / {engine.url.database}")
    try:
        changes = check(engine) if args.check else migrate(engine)
    except MigrationError as error:
        print(f"SCRUM-64 preflight failed: {error}")
        return 1
    except SQLAlchemyError as error:
        print(f"Database operation failed ({type(error).__name__}); no credentials printed. Review database diagnostics locally.")
        return 1
    if args.check:
        print("\n".join(changes) if changes else "SCRUM-64 schema is compatible; no migration required")
        return 1 if changes else 0
    print("\n".join(changes) if changes else "Already compatible; no changes applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
