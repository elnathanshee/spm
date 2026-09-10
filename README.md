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
| Backend | Python (FastAPI) | Agreed |
| Version control | GitHub | Agreed |
| Hosting | Vercel (auto-deploys from GitHub on push) | Agreed |
| Frontend | TBC | Open |
| Database | TBC | Open |
| Authentication | TBC | Open |
| Testing framework | TBC | Open |
| CI | TBC | Open |

*(Update this table as decisions are made in sprint planning — keep it as the single source of truth for the stack.)*

## Getting Started

### Prerequisites

- Python 3.x
- Node.js (once frontend framework is chosen)
- Git

### Setup

```bash
git clone https://github.com/<org-or-user>/<repo-name>.git
cd <repo-name>

# backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# frontend (once added)
cd frontend
npm install
```

### Environment Variables

Copy `.env.example` to `.env` and fill in the required values (database URL, auth keys, etc.). Never commit `.env` itself.

### Running Locally

```bash
# backend
uvicorn main:app --reload

# frontend (once added)
npm run dev
```

### Running Tests

```bash
# backend
pytest

# frontend (once added)
npm test
```

## Deployment

Every push to `main` auto-deploys via Vercel. No manual deployment step should be needed.

## Project Management

- **Backlog, sprints, estimates:** Jira, project `SCRUM` ("ConnectSphere Event Services") — internal working source; exported for the Week 12 submission (Deliverable 1).
- **Team agreements, meeting notes, sprint evidence:** shared team Drive.

## Repository Access

Both course instructors have been granted collaborator access to this repository.

## License

Coursework project for IS212, Singapore Management University. Not for external distribution.
