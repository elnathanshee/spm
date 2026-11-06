from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import router as auth_router
from .coordinator import router as coordinator_router
from .database import engine
from .equipment import router as equipment_router  # [SCRUM-39 | Melvin]
from .events import router as events_router
from .lead import router as lead_router
from .models import Base
from .notifications import router as notifications_router
from .suitability import router as suitability_router
from .venue_calendar import router as venue_calendar_router
from .venue_holds import router as venue_holds_router, run_hold_checks  # [SCRUM-81 | Elnathan]
from .venues import router as venues_router
# [Sprint 5] B SCRUM-82 router import: insert directly below this line.
from .venue_unavailability import router as venue_unavailability_router
# [Sprint 5] C SCRUM-102 router import: insert directly below this line.
from .lifecycle import router as lifecycle_router
from .safety import router as safety_router
# [Sprint 5] D SCRUM-41 router import: insert directly below this line.
from .equipment_catalogue import router as equipment_catalogue_router
# [Sprint 5] E SCRUM-45 router import: insert directly below this line.
# [Sprint 5] F SCRUM-49 router import: insert directly below this line.

# [SCRUM-81] Hold expiry needs no timer: the check that sends hold warnings and
# expiry notices, and stores "Expired", runs at the start of every request on
# these routers (one query; it commits only when something changed).
HOLD_CHECKS = [Depends(run_hold_checks)]

# Create all database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="ConnectSphere API",
    description="ConnectSphere Event Planning & Venue Booking Backend API",
    version="1.0.0",
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(events_router, dependencies=HOLD_CHECKS)
app.include_router(coordinator_router)
app.include_router(equipment_router)  # [SCRUM-39 | Melvin]
app.include_router(lead_router)
app.include_router(notifications_router, dependencies=HOLD_CHECKS)
app.include_router(venues_router, dependencies=HOLD_CHECKS)
app.include_router(venue_calendar_router)
app.include_router(suitability_router)
app.include_router(venue_holds_router, dependencies=HOLD_CHECKS)  # [SCRUM-81]
# [Sprint 5] B SCRUM-82 include_router: insert directly below this line.
app.include_router(venue_unavailability_router, dependencies=HOLD_CHECKS)
# [Sprint 5] C SCRUM-102 include_router: insert directly below this line.
app.include_router(lifecycle_router, dependencies=HOLD_CHECKS)
app.include_router(safety_router)
# [Sprint 5] D SCRUM-41 include_router: insert directly below this line.
app.include_router(equipment_catalogue_router)
# [Sprint 5] E SCRUM-45 include_router: insert directly below this line.
# [Sprint 5] F SCRUM-49 include_router: insert directly below this line.


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "ConnectSphere Backend API",
        "docs_url": "/docs",
    }
