from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import router as auth_router
from .coordinator import router as coordinator_router
from .database import engine
from .events import router as events_router
from .lead import router as lead_router
from .models import Base
from .notifications import router as notifications_router
from .reassignment import router as reassignment_router
from .suitability import router as suitability_router
from .venue_calendar import router as venue_calendar_router
from .venues import router as venues_router

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
app.include_router(events_router)
app.include_router(coordinator_router)
app.include_router(lead_router)
app.include_router(notifications_router)
app.include_router(reassignment_router)
app.include_router(venues_router)
app.include_router(venue_calendar_router)
app.include_router(suitability_router)


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "ConnectSphere Backend API",
        "docs_url": "/docs",
    }
