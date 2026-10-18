# spm
# ConnectSphere

Event Planning & Venue Booking System, built for **IS212 Software Project Management** (SMU, AY2026/27 Term 1).

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

## Tech Stack

| Layer | Choice | Status |
|---|---|---|
| Frontend | Alpine.js + Tailwind CSS (static assets) | Agreed |
| Backend | Python (FastAPI) | Agreed |
| Database | SQLite (Dev) / PostgreSQL (Neon / Prod) via SQLAlchemy | Agreed |
| Authentication | Local Session Cookie (`connectsphere_session`) with bcrypt | Agreed |
| Testing framework | Pytest + FastAPI TestClient | Agreed |
| Version control | GitHub | Agreed |
| Hosting | Vercel (auto-deploys from GitHub on push) | Agreed |
| CI | GitHub Actions | Open |

*(Update this table as decisions are made in sprint planning — keep it as the single source of truth for the stack.)*

## Getting Started

### Prerequisites

- Python 3.x
- Node.js and npm
- Git

### Setup

```bash
git clone https://github.com/<org-or-user>/<repo-name>.git
cd <repo-name>

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

Open `http://localhost:5173`. Vite proxies `/auth`, `/events`, and
`/coordinator`, `/lead`, and `/notifications` to `http://localhost:8000`, preserving same-origin cookie
behavior during development.

Credentialed CORS is currently enabled for `http://localhost:3000`,
`http://127.0.0.1:3000`, `http://localhost:5173`, and
`http://127.0.0.1:5173`. Serve the frontend over HTTP rather than opening
`frontend/index.html` with `file://`, so the Vite proxy and HttpOnly session
cookie are available.

### Running Tests

```bash
# backend
python -m pytest

# frontend, from the frontend directory
npm test
npm run build
```

See [docs/SCRUM-17-MANUAL-TESTING.md](docs/SCRUM-17-MANUAL-TESTING.md) for
development-account initialization and the six-role RBAC verification guide.

## Deployment

Every push to `main` auto-deploys via Vercel. No manual deployment step should be needed.

Configure Vercel with the project root set to `frontend/`, Build Command set to
`npm run build`, and Output Directory set to `dist`. The deployed frontend
must use a backend URL whose CORS policy allows the Vercel origin with
credentials enabled.

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
- **[C4 Architecture DSL](docs/connectsphere.dsl):** Structurizr DSL architecture models for System Context and Container views.
- **[Sequence Diagrams](docs/diagrams/):** Mermaid sequence diagrams and high-res exports for core event lifecycles.

## Project Management

- **Backlog, sprints, estimates:** Jira, project `SCRUM` ("ConnectSphere Event Services") — internal working source; exported for the Week 12 submission (Deliverable 1).
- **Team agreements, meeting notes, sprint evidence:** shared team Drive.

## Repository Access

Both course instructors have been granted collaborator access to this repository.

## License

Coursework project for IS212, Singapore Management University. Not for external distribution.
