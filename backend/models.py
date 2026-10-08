from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # e.g., 'organiser', 'coordinator', 'admin'
    is_active_coordinator = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    sessions = relationship("LoginSession", back_populates="user", cascade="all, delete-orphan")
    organised_events = relationship("Event", foreign_keys="Event.organiser_id", back_populates="organiser")
    assigned_events = relationship("Event", foreign_keys="Event.assigned_coordinator_id", back_populates="assigned_coordinator")
    coordinator_assignments = relationship("CoordinatorAssignmentHistory", back_populates="coordinator")
    notifications = relationship("Notification", back_populates="recipient", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    purpose = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    proposed_start = Column(DateTime, nullable=True)
    expected_attendance = Column(Integer, nullable=True)
    venue_requirements = Column(Text, nullable=True)
    accessibility_needs = Column(Text, nullable=True)
    equipment_requirements = Column(Text, nullable=True)
    registration_needs = Column(Text, nullable=True)
    organiser_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(50), nullable=False, default="Draft")
    assigned_coordinator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = Column(DateTime, nullable=True)

    # Relationships
    organiser = relationship("User", foreign_keys=[organiser_id], back_populates="organised_events")
    assigned_coordinator = relationship("User", foreign_keys=[assigned_coordinator_id], back_populates="assigned_events")
    assignment_history = relationship("CoordinatorAssignmentHistory", back_populates="event", cascade="all, delete-orphan")
    status_history = relationship("EventStatusHistory", back_populates="event", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="event", cascade="all, delete-orphan")


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(255), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    valid = Column(Boolean, default=True, nullable=False)

    # Relationship
    user = relationship("User", back_populates="sessions")


class CoordinatorAssignmentHistory(Base):
    __tablename__ = "coordinator_assignment_history"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    coordinator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="assignment_history")
    coordinator = relationship("User", back_populates="coordinator_assignments")


class EventStatusHistory(Base):
    __tablename__ = "event_status_history"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    previous_status = Column(String(50), nullable=True)
    new_status = Column(String(50), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    reason = Column(Text, nullable=True)

    # Relationships
    event = relationship("Event", back_populates="status_history")
    actor = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read = Column(Boolean, default=False, nullable=False)

    recipient = relationship("User", back_populates="notifications")
    event = relationship("Event", back_populates="notifications")
