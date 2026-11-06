# ConnectSphere

Event Planning & Venue Booking System, built for **IS212 Software Project Management** (SMU, AY2026/27 Term 1).

- **Repository:** https://github.com/elnathanshee/spm
- **Live app:** https://spm-dusky-six.vercel.app
- **Release:** [v1.0.0](https://github.com/elnathanshee/spm/releases/tag/v1.0.0), the end of Sprint 4 (5 Nov 2026)

## What's new in v1.1.0 (Sprint 5)

<!-- [Sprint 5] A SCRUM-83 multiple venues: one bullet directly below this line. -->
<!-- [Sprint 5] B SCRUM-82 venue unavailability: one bullet directly below this line. -->
<!-- [Sprint 5] C SCRUM-102 lifecycle and safety: one bullet directly below this line. -->
- **Event lifecycle and safety check:** Approved events move through Planning, a Safety Officer's check, Confirmed and Completed, and can be cancelled with a reason before Completed.
<!-- [Sprint 5] D SCRUM-41 equipment: one bullet directly below this line. -->
<!-- [Sprint 5] E SCRUM-45 registration: one bullet directly below this line. -->
<!-- [Sprint 5] F SCRUM-49 change requests: one bullet directly below this line. -->
<!-- [Sprint 5] G SCRUM-85 Lead oversight: one bullet directly below this line. -->

## Team — Machu Picchu

| Name | Role |
|---|---|
| Elnathan | Product Owner |
| Renee | Scrum Master |
| Darryl | Developer |
| Sarah | Developer |
| Melvin | Developer |

## Product Goal

> ConnectSphere staff and clients can take an event from request through to a confirmed, resourced, open-for-registration event, entirely in the system, with changes handled without arrangements silently going stale.

## Final sprint: Sprint 4 (24 Oct to 4 Nov 2026)

> **Sprint Goal:** Make the core ConnectSphere workflow usable end-to-end through the UI. A coordinator is able to capture an event request, find an available venue, and request it. Reviewers and venue and tech staff can then act on that request and booking from their own screens. All of this ships on a CI-checked frontend. Ensure every backend has a corresponding frontend.

## Tech Stack

| Layer | Choice | Status |
|---|---|---|
| Frontend | Alpine.js + Tailwind CSS (static assets) | Agreed |
| Backend | Python (FastAPI) | Agreed |
| Database | SQLite (Dev) / PostgreSQL (Neon / Prod) via SQLAlchemy | Agreed |
| Authentication | Local Session Cookie (`connectsphere_session`) with bcrypt | Agreed |
| Testing framework | Pytest + FastAPI TestClient (backend); Node.js test runner (frontend) | In use |
| Version control | GitHub | Agreed |
| Hosting | Vercel: one project with a frontend and a backend service on one domain, deployed from `main` | In use |
| CI | GitHub Actions: backend tests with branch coverage; frontend tests and build | In use |

*(Update this table as decisions are made in sprint planning — keep it as the single source of truth for the stack.)*

## Getting Started

### Prerequisites

- Python 3.x
- Node.js and npm
- Git

### Setup

```bash
git clone https://github.com/elnathanshee/spm.git
cd spm

# backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# frontend
cd frontend
npm install
```

### Environment Variables

Copy `.env.example` to `.env` and fill in the required values (database URL, auth keys, etc.). Never commit `.env` itself.

### Running Locally

```bash
# From the frontend directory; starts both FastAPI and Vite
cd frontend
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/auth`, `/events`, `/venues`, `/equipment-requests`, and
`/coordinator`, `/lead`, and `/notifications` to `http://localhost:8000`, preserving same-origin cookie
behavior during development.

The backend's `GET /coordinator/events` (what `/coordinator/events` without the `#` reaches) is SCRUM-24's Sprint 1 server-rendered page, deprecated and kept as that story's test evidence; the app's Coordinator screen reads `/coordinator/events/data`.
`coordinator_page_preview.html` in the repository root is a static snapshot of that page, kept as Sprint 1 evidence.

Credentialed CORS is currently enabled for `http://localhost:3000`,
`http://127.0.0.1:3000`, `http://localhost:5173`, and
`http://127.0.0.1:5173`. Serve the frontend over HTTP rather than opening
`frontend/index.html` with `file://`, so the Vite proxy and HttpOnly session
cookie are available.

### Running Tests

```bash
# backend (from repository root)
python -m pytest

# frontend (from frontend directory)
cd frontend
npm test
npm run build
```

See [docs/SCRUM-17-MANUAL-TESTING.md](docs/SCRUM-17-MANUAL-TESTING.md) for
development-account initialization and the six-role RBAC verification guide.

## Deployment

The app is live at **https://spm-dusky-six.vercel.app**.

It runs on Vercel as one project with two services, defined in [`vercel.json`](vercel.json):

| Service | What it runs | Public paths |
|---|---|---|
| `frontend` | The Vite build of `frontend/` | `/` and every path not listed for the backend |
| `backend` | The FastAPI app `backend.main:app`, from the repository root with `requirements.txt` | `/auth`, `/events`, `/venues`, `/coordinator`, `/lead`, `/notifications`, `/equipment-requests` |

Both services share one domain. The browser calls the API with relative paths, so the `SameSite=Lax` session cookie works as it does locally, and the backend paths are the same ones the Vite dev proxy forwards. On Vercel the session cookie is also marked `Secure`.

- **Deploys:** every push to `main` deploys production. Each pull request gets a preview deployment, which only members of the Vercel project can open.
- **Environment variable:** set `DATABASE_URL`, the Neon PostgreSQL connection string, in the Vercel project's Environment Variables for Production and Preview. On Vercel the backend stops with a clear error if it is missing, instead of falling back to a SQLite file. Leave `VITE_API_BASE_URL` unset.
- **Accounts:** users are created with `backend/seed_users.py`, which takes the test-account password from the `CONNECTSPHERE_DEV_PASSWORD` environment variable.
- **Existing databases:** before pointing a deployment at one, run the read-only `--check` of `backend/migrate_scrum64.py` and `backend/migrate_scrum95.py` against it ([SCRUM-64](docs/SCRUM-64-MIGRATION.md), [SCRUM-95](docs/SCRUM-95-UTC-MIGRATION.md)).

To deploy a copy: import the repository on Vercel as one project with Services (root directory `./`), add `DATABASE_URL`, then deploy.

## Venue booking workflow

Venue Staff use **Venue operations** to add a venue (name, location, capacity),
view venues and booking requests, and review a pending request. Approval uses the
existing booking endpoint; rejection requires a reason. All Venue Staff can
manage all venues. No venue-to-staff assignment is required.

Coordinators use **Assigned requests > Venue bookings**; Leads use **Assignment
oversight > Venue bookings**. Refresh the booking dialog to load venues added
since it was opened. The same dialog submits requests and displays status and
rejection reasons for both roles.

The Venue Staff dashboard reuses the existing 15-second notification polling.
Unread messages are highlighted; staff can mark their own messages as read and
open the corresponding booking. Approval/rejection messages continue to reach
the original requester. Historical bookings without a recorded requester display
that fact and do not infer a requester from the current event assignment.

Venue creation/review are supporting workflow functionality. Notification display,
read handling, and notification-to-booking links are the SCRUM-64 integration.
The backend decision and notification delivery rules are unchanged. Automated
HTTP integration tests use disposable SQLite; running the application against
Neon and creating or deciding records changes live data.

## Database initialization

Application startup initializes missing tables from the SQLAlchemy models with
`Base.metadata.create_all()`. A fresh empty database receives the SCRUM-64 requester
reference and notification uniqueness constraint without a separate migration.
This does not upgrade existing tables. **Before deploying SCRUM-64, the database
owner must run `python -m backend.migrate_scrum64 --check` against the intended
target and obtain a successful result.** Older databases can be upgraded with the
restored, explicit `--apply` command only after approval, a verified recovery point,
and stopped application writers. The configured development/test Neon branch was
verified compatible on 19 Oct 2026; no migration was applied during that check.
Other branches must be checked independently. See
[SCRUM-64 migration and verification](docs/SCRUM-64-MIGRATION.md) for the required
columns, constraints, evidence, Windows commands, and deployment order.

## Venue Holds and Expiry (SCRUM-81)

A venue booking request is a **tentative hold** (status `Pending`) until Venue Staff approve or reject it. Holds expire, so a venue is never reserved indefinitely.

- **Default expiry:** 48 hours after the request, or the booking's start if that is sooner. It is stored with the request. Venue Staff can set or extend it with `PUT /venues/bookings/{booking_id}/hold`.
- **After expiry:** the hold stops blocking the venue (search, new requests, the calendar and retire all treat it as free), it shows as `Expired`, and it can no longer be approved, rejected or extended. The Coordinator must submit a new request if they still need the venue. Confirmed bookings never expire.
- **Coordinator notices (in-app):** a warning before expiry, 24 hours ahead by default, which the event's assigned Coordinator can change per hold with `PUT /venues/bookings/{booking_id}/hold/warning`; and a notice at expiry. Each is sent once.
- **When notices are created:** there is no background job (the app runs on serverless hosting). Due notices are created at the start of any request to the events, notifications or venues API, which includes every signed-in user's notification poll (every 15 seconds in the web app). Any user's activity creates everyone's due notices.
- **Limitation:** if nobody uses the app between a hold's warning time and its expiry, only the expiry notice is shown.

<!-- [Sprint 5] A SCRUM-83 multiple venues: its section directly below this line. -->
<!-- [Sprint 5] B SCRUM-82 venue unavailability: its section directly below this line. -->
<!-- [Sprint 5] C SCRUM-102 lifecycle and safety: its section directly below this line. -->
## Event lifecycle and safety check (SCRUM-102, 86, 87, 66)

- **Coordinator:** starts planning, submits for the safety check once there is a Confirmed booking and all equipment is arranged, and marks a Confirmed event Completed after it ends.
- **Safety Officer** (`safety_officer` or `safety officer`, page `/safety`): sees attendance, venue capacity and layouts, accessibility needs, equipment notes and known venue restrictions, then approves, rejects or requests changes. A reason is required unless approving. Only approval reaches Confirmed, and it is refused if the arrangements stopped meeting the submit rule during the check.
- **Reject or request changes:** the event returns to Planning and its bookings and equipment are flagged for review. Nothing is cancelled. Resubmitting clears the flags, and the rows stay as history.
- **Cancelling:** the assigned Coordinator or the Lead at any stage before Completed; the owning Organiser only before Approved. A reason is required. Bookings become Cancelled, review flags clear, and the rows stay. A Cancelled or Completed event can't take a new booking or equipment line.
- **Notices:** the Organiser and the Coordinator are told when an event is confirmed or cancelled. The Coordinator is told when a check returns it.
- **Endpoints:** `POST /events/{id}/start-planning`, `/submit-safety`, `/complete`, `/cancel`; `GET /events/{id}/lifecycle-actions`; `GET /events/{id}/review-flags`, `POST /events/{id}/review-flags/clear`; `GET /safety/events`, `GET /safety/events/{id}`, `POST /safety/events/{id}/decision`.

<!-- [Sprint 5] D SCRUM-41 equipment: its section directly below this line. -->
<!-- [Sprint 5] E SCRUM-45 registration: its section directly below this line. -->
<!-- [Sprint 5] F SCRUM-49 change requests: its section directly below this line. -->
<!-- [Sprint 5] G SCRUM-85 Lead oversight: its section directly below this line. -->

## Developer Standards & SOP

To maintain code quality, TDD commit traceability, and trunk stability, all team members must follow the **[ConnectSphere Developer SOP](docs/DEVELOPER_SOP.md)**:

1. **Trunk-Based Development:** Short-lived feature branches (`feature/SCRUM-<id>-<name>`) merging into `main` via PRs.
2. **Test-Driven Development (TDD):** Two-phase commit discipline for every story:
   - `test(SCRUM-XX): ...` (RED Phase)
   - `feat(SCRUM-XX): ...` (GREEN Phase)
3. **5-Path Test Classification:** Every feature must test Happy Path, Negative Path, Boundary Path, Workflow Variations, and Cross-Cutting concerns.

## Documentation & Architecture

- **[Developer SOP & TDD Guide](docs/DEVELOPER_SOP.md):** Team coding standards, branching, and TDD commit rules.
- **[Test Case Register](docs/TEST_CASES.md):** Complete log of automated test cases, scenarios, and results across all sprints.
- **[C4 model](docs/connectsphere.dsl):** Structurizr DSL for the C1 System Context and C2 Container views and the production deployment. Rendered PNGs are in [docs/c4/](docs/c4/).
- **[Sequence Diagrams](docs/diagrams/):** Mermaid sequence diagrams and high-res exports for core event lifecycles.

## Project Management

- **Backlog, sprints, estimates:** Jira, project `SCRUM` ("ConnectSphere Event Services") — internal working source; exported for the Week 12 submission (Deliverable 1).
- **Team agreements, meeting notes, sprint evidence:** shared team Drive.

## Repository Access

Repository: https://github.com/elnathanshee/spm. It moved from `reneelui/spm` in November 2026, and the old address redirects. The repository is public, and both course instructors (cposkitt and kokkhinglee) were invited as collaborators on 6 Nov 2026.

## License

Coursework project for IS212, Singapore Management University. Not for external distribution.
