# SCRUM-95: move existing bookings to UTC (one-off)

Before SCRUM-95, `venue_bookings.booking_start` / `booking_end` held the Singapore
wall time the user typed. SCRUM-95 (D7) stores them in UTC, so rows saved before
it display and clash-check 8 hours late.

`backend/migrate_scrum95.py` subtracts 8 hours from every booking whose
`created_at` (already UTC) is before the SCRUM-95 deploy time, and pulls any hold
expiry that is now after the real start back to it (the SCRUM-81 cap). It runs in
one transaction and records itself in a `data_migrations` table, so a second run
is refused and no row is shifted twice.

If the team agrees the current data can be reset instead, skip this script.

## Run it

Take a backup (Neon branch or `pg_dump`) and stop writers first. Use the deploy
time of SCRUM-95 in UTC, with no offset.

```bash
python -m backend.migrate_scrum95 --check --created-before 2026-11-03T02:00
```

```bash
python -m backend.migrate_scrum95 --apply --created-before 2026-11-03T02:00
```

`--check` is read-only and prints how many bookings would move. Tests use
disposable SQLite only: `backend/tests/test_scrum95_utc_migration.py` (TC-9524).
