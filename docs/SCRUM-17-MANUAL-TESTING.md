# SCRUM-17 manual testing guide

## Setup and development accounts

Create the repository virtual environment at the repository root and install dependencies:

```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm install
```

Choose a development-only password in the current PowerShell session. Do not commit it:

```powershell
$env:CONNECTSPHERE_DEV_PASSWORD = Read-Host -MaskInput "Development test password"
cd ..
.\venv\Scripts\python.exe -m backend.seed_users
```

The seed is repeatable: it creates missing usernames and never overwrites existing accounts or passwords.

| Role | Backend identifier | Username | Workspace |
|---|---|---|---|
| Organiser | `organiser` | `organiser1` | `/#/events` |
| Event Coordinator | `coordinator` | `coord1` | `/#/coordinator/events` |
| Event Coordinator Lead | `lead` | `lead1` | `/#/lead/events` |
| Venue Staff | `venue staff` | `venue1` | `/#/venue` |
| Technical Support | `technical support` | `technical1` | `/#/technical` |
| Attendee | `attendee` | `attendee1` | `/#/attendee` |

Existing accounts retain their old password. To replace a forgotten development password, update only that local record with a newly generated hash; do not put a real password in source control.

Start the whole application with one command:

```powershell
cd frontend
npm.cmd run dev
```

Open `http://localhost:5173/`. FastAPI documentation is at `http://127.0.0.1:8000/docs`.

## Browser role checks

For each account, sign in from `/`, confirm the expected workspace above, and manually enter every other role URL in the hash. Each forbidden URL must show the explicit **Access denied** view.

1. `organiser1`: create, edit, and submit its own request; confirm Coordinator and Lead pages are denied.
2. `coord1`: confirm only assigned requests appear; confirm Organiser creation and Lead assignment pages are denied.
3. `lead1`: confirm the assignment workspace, unassigned requests, active Coordinator selector, assignment and reassignment; confirm Coordinator decision controls are unavailable.
4. `venue1`: confirm `/venue`; deny `/events`, `/coordinator/events`, `/lead/events`, `/technical`, and `/attendee`.
5. `technical1`: confirm `/technical`; deny `/events`, `/coordinator/events`, `/lead/events`, `/venue`, and `/attendee`.
6. `attendee1`: confirm `/attendee`; deny every internal planning and assignment page.

Capture the landing page and one explicit access-denied page for each role. Also capture the Lead unassigned queue, initial assignment, reassignment, the old Coordinator losing the request, the new Coordinator receiving it, and both reassignment notifications.

## Preparing an assignable event

Log in as `organiser1`, then create a request through the UI and submit it. For Swagger, use `POST /events` with this actual `EventCreate` schema:

```json
{
  "title": "SCRUM-17 manual verification",
  "purpose": "Peer review evidence",
  "description": "Verify Lead assignment and Coordinator ownership",
  "proposed_start": "2026-12-01T10:00:00+08:00",
  "expected_attendance": 40,
  "venue_requirements": "Seminar room",
  "accessibility_needs": "Step-free access",
  "equipment_requirements": "Projector and microphones",
  "registration_needs": "Attendee list"
}
```

Expected: `201`. Copy the returned `id`, then call `POST /events/{event_id}/submit` with no body. Expected: `200`, status `Submitted`, and `assigned_coordinator_id: null`.

## Swagger UI authorization tests

Authentication uses the HttpOnly `connectsphere_session` cookie rather than Swagger's **Authorize** button.

1. In Swagger, run `POST /auth/login` with `{"username":"lead1","password":"<your development password>"}`. Expected: `200`.
2. Run `GET /auth/me`. The response identifies the account whose cookie Swagger is currently using.
3. Run `GET /lead/events` and `GET /lead/coordinators`. Expected for Lead: `200`.
4. Note the event ID and the intended Coordinator's numeric ID.
5. Initial assignment: `POST /events/{event_id}/assign` with `{"coordinator_id": <coord1-id>}`. Expected: `200`.
6. Reassignment: `POST /events/{event_id}/reassign` with `{"coordinator_id": <coord2-id>}`. Expected: `200`.
7. Log in as `coord1` and call `POST /events/{event_id}/approve` with `{}`. After reassignment, expected: `403`.
8. Log in as `coord2` and use `POST /events/{event_id}/review` with `{"reason":"Manual review started"}`. Expected: `200`. Then approve with `POST /events/{event_id}/approve` and `{"reason":"Requirements verified"}`; expected: `200`.
9. A rejection instead uses `POST /events/{event_id}/reject` with `{"reason":"Required reason"}`. A return uses `POST /events/{event_id}/return` with `{"reason":"Required clarification"}`. These require a separate event still in `Submitted` or `Under Review` state.
10. Run `GET /notifications` as each Coordinator to verify assignment messages.

Wrong-role access to Lead, Coordinator, or event-management operations should return `403`. Missing or invalid authentication should return `401`. Unknown event IDs should return `404`; invalid state transitions or assignees return `400`; schema validation failures return `422`.

To switch accounts safely, run `POST /auth/logout` (expected `200`), then run `GET /auth/me` (expected `401`) before logging in as the next user. A new private browser window is another safe way to isolate the cookie.

## Legacy Admin cleanup

Admin is not a supported application role. Existing Admin rows are deliberately not deleted or converted. They receive `403` at login and from authenticated endpoints. After taking a database backup, a database owner may explicitly disable or remove obsolete development-only Admin rows according to the deployment's retention policy. Never apply an automatic role conversion.

## Automated verification

```powershell
# repository root
.\venv\Scripts\python.exe -m pytest -q

# frontend directory
cd frontend
npm.cmd test
npm.cmd run build
```
