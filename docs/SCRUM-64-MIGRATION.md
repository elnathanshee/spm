# SCRUM-64: Neon schema compatibility (PR review Point 1)

## Required schema

- `venue_bookings.requester_id`: nullable integer FK to `users.id`, with no default.
  Submission records the authenticated Coordinator/Lead. Historical NULL values
  are preserved; outcome notifications do not guess a requester from reassignment.
- `notifications.booking_id`: nullable integer FK to `venue_bookings.id`, with no
  default. Existing event notifications keep NULL booking IDs.
- A full, valid UNIQUE constraint/index on `(booking_id, kind, user_id)` (any column
  order/name), with ordinary NULL-distinct semantics. Partial, nonunique, invalid,
  or NULLS NOT DISTINCT indexes are not substitutes. Multiple event notifications
  with NULL booking IDs remain permitted.

SQLAlchemy `create_all()` creates missing tables, not columns/constraints on
existing tables. A fresh empty database receives this schema from the models;
existing databases must pass the predeployment check below. No booking route,
notification semantics, frontend, or application initialization behavior changes
in this fix.

## Read-only verification evidence: 19 Oct 2026

The configured target matched the repository `.env` hostname and database:

- Host: `ep-blue-rice-b35d7rw3-pooler.c-4.ap-southeast-1.aws.neon.tech`
- Database/schema: `neondb` / `public`
- Neon project ID: `soft-hill-82104655`
- Neon branch ID: `br-curly-cloud-b30ywvub` (previously confirmed by the user as the
  intended development/test branch; this check reconfirmed the server IDs).
- Both required columns exist as nullable INTEGERs.
- `venue_bookings_requester_id_fkey` references `users.id` and is validated.
- `notifications_booking_id_fkey` references `venue_bookings.id` and is validated.
- `uq_notification_booking_recipient_kind` is UNIQUE on `(user_id, booking_id,
  kind)`, full/nonpartial, valid and ready; NULLS NOT DISTINCT is false.

**This target already contains the required changes. No Neon migration was run
by this task and no shared records/schema were changed.** This is evidence of
current compatibility, not evidence that the removed script was executed. No
committed history for the old script path was found, and there is no migration
execution audit record establishing who applied these changes or when. Do not
claim the old migration ran based on the current schema alone.

The database also has unrelated columns/constraints (for example
`requested_by_id`); this migration leaves them alone. Other Neon branches and
production must be checked independently; this finding does not approve writes.

## Resolution choice

Restore one additive, explicit migration rather than redesign booking provenance
and notification linkage into new tables. SCRUM-80 (`VenueBookingConflictFlag`)
and SCRUM-81 (`VenueHold`) use new tables because `create_all()` can add them.
Following that pattern here would require new models, joins, linkage, and changed
notification code despite the current target already being compatible. It adds
complexity without resolving a present incompatibility more simply.

The migration checks schema and existing data before DDL, adds only missing
nullable columns/FKs and the unique index, validates existing PostgreSQL NOT VALID
FKs, and rechecks before commit. Equivalent existing indexes are reused regardless
of name. PostgreSQL operations are transactional, serialize with short table-lock
and statement timeouts, and fail rather than wait indefinitely. SQLite is
supported for isolated regression tests with an explicit DDL transaction; an
existing SQLite column missing its FK is refused rather than rebuilding a table.
There is no automatic startup upgrade or new migration dependency/framework.

The operator must resolve duplicate booking notifications, orphan references,
wrong column definitions, unexpected FKs or conflicting index names separately.
The migration never deletes, merges, updates, or backfills records. Historical
requesters stay unknown. Rerunning a completed migration is a no-op.

## Required deployment order and Windows commands

From `C:\Users\DaringHero\Developer\GitHub\spm`, using the existing venv:

```powershell
.\venv\Scripts\python.exe -m backend.migrate_scrum64 --check
```

This command is read-only, does not import `backend.main`, prints only the host
and database (no credentials), and exits 0 only when compatible. It exits 1 for
missing schema, incompatible data/schema, or connection failure. The environment's
DATABASE_URL overrides `.env`; confirm the printed target and Neon project/branch
in the Neon console. Do not paste credentials into review comments or logs.

If the check passes, skip `--apply` and deploy the backend/frontend normally.
If changes are required, **do not deploy SCRUM-64 yet**:

1. The PR author must coordinate with the Neon database owner and obtain explicit
   approval for the specific target; production requires explicit production approval.
2. The owner must verify a recoverable backup or named Neon recovery branch and
   record its identity, and stop all application writers/scheduled jobs.
3. Review the preflight output/data constraints. Do not resolve duplicates or
   orphan records automatically. Record pre-upgrade row counts or a data snapshot
   in the approved environment for comparison; never reset existing tables.
4. The approved database owner (not this automated task) applies the migration:

```powershell
.\venv\Scripts\python.exe -m backend.migrate_scrum64 --apply
.\venv\Scripts\python.exe -m backend.migrate_scrum64 --check
```

5. Verify existing records against the recorded snapshot, retain check output in
   the deployment record, then restart/deploy the backend and frontend. Smoke-test
   submission, staff notification, approval/rejection, and original-requester
   outcome notification in the approved test environment.

No backup or stopped-writer claim is made by this task: these are prerequisites
for a future `--apply`. This target currently needs no upgrade and no approval is
needed for the read-only verification. The command requires explicit `--check` or
`--apply`; the old no-argument command is intentionally not an implicit write.

## Testing

```powershell
.\venv\Scripts\python.exe -m pytest backend/tests/test_scrum64_migration.py backend/tests/test_scrum64_booking_notifications.py backend/tests/test_scrum35_booking_request.py backend/tests/test_scrum36_booking_decision.py backend/tests/test_scrum37_rejection_reason.py backend/tests/test_scrum51_notifications.py -q -p no:cacheprovider --disable-warnings
& 'C:\Program Files\nodejs\node.exe' --test --test-isolation=none frontend/tests/*.test.js
Push-Location frontend
try { & 'C:\Program Files\nodejs\npm.cmd' run build } finally { Pop-Location }
git diff --check
```

Pytest is explicitly isolated from Neon by root `conftest.py`. Migration tests use
populated legacy SQLite schemas, fresh model schemas, and the actual FastAPI
submission/decision APIs. They cover preservation, requester/booking linkage,
deduplication, repeat runs, equivalent index names, and refusal paths.
The PostgreSQL preflight is also executed read-only against Neon. Applying the
upgrade to a separate PostgreSQL instance/Neon branch remains an operator rehearsal
if an older target ever needs changes; no PostgreSQL DDL was run in this task.

## Verification results (19 Oct 2026)

- Required Neon schema verified read-only; `--check` exits 0.
- 69 backend tests passed, including 10 migration tests and booking/notification regressions.
- 37 frontend tests passed; production build and `git diff --check` passed.
- The initial build could not find Vite. Restoring existing lockfile dependencies
  with `npm ci --include=dev --ignore-scripts --no-audit --no-fund` resolved the
  environment issue; package.json and package-lock.json were unchanged.
- No application code/frontend changes, commits, pushes, or Neon writes were made.
- PostgreSQL DDL was not executed: no local server binaries were available and
  Docker was not running. Legacy upgrade writes were exercised in isolated SQLite;
  the actual Neon PostgreSQL checks were read-only. This limitation must not be
  represented as a PostgreSQL upgrade rehearsal.

## Draft reviewer response (Point 1 only)

Verified the configured Neon target read-only on 19 Oct: project
`soft-hill-82104655`, database `neondb`, branch `br-curly-cloud-b30ywvub`. Both nullable
columns and validated FKs exist, and `uq_notification_booking_recipient_kind` is a
valid full unique index on recipient/booking/kind with normal NULL semantics.
This branch therefore needs no migration. I cannot establish whether the removed
script was previously run; the evidence establishes current schema state only.
I restored an explicit, idempotent migration and read-only preflight for older
branches, with preservation/refusal regression tests and a documented mandatory
check before deployment. No Neon writes were performed. Any incompatible target
must be upgraded by the approved database owner after backup verification and
stopping writers, before deployment.
