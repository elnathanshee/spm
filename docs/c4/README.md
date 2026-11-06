# C4 model (C1, C2 and production deployment)

The model is [`../connectsphere.dsl`](../connectsphere.dsl) (Structurizr DSL), updated on 5 Nov 2026 from the released code: `backend/main.py` and its routers, the roles in `backend/auth.py`, `backend/database.py`, `frontend/`, `vercel.json` and `.github/workflows/ci.yml`.

| File | View |
|---|---|
| `C1_SystemContext.png` | C1 System Context: the six roles the release supports and ConnectSphere. No external system at run time. |
| `C2_Containers.png` | C2 Containers: Web Application (Alpine.js, Tailwind, Vite), Backend API (FastAPI) and Database (PostgreSQL on Neon). |
| `Deployment_Production.png` | Production: one Vercel project with a `frontend` and a `backend` service on one domain, the database on Neon, and GitHub with Actions CI. |

We stop at C2, as the Team Working Agreements and the Week 4 lab advise.

The PNGs are drawn with Graphviz by `render_c4.py`, which holds the same names, descriptions and relationships as the DSL (`python3 render_c4.py .`). The DSL renders the same views at https://playground.structurizr.com.

Not in the model, because the release does not have them: the Safety Officer role and its step (Week 7 change 6), attendee registration, and an email or SMS gateway. Notifications are in-app only.
