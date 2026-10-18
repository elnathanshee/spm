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
from sqlalchemy.orm import backref, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # e.g., 'organiser', 'coordinator', 'lead', 'admin'
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
    venue_bookings = relationship("VenueBooking", back_populates="event", cascade="all, delete-orphan")


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


# [SCRUM-51 | Renee] NEW: in-app notifications. The customer left the delivery
# channel to the team, so a notification is a row the recipient reads through
# GET /notifications. SCRUM-63 and later stories reuse this table.
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True, index=True)
    kind = Column(String(50), nullable=True)  # e.g. 'submitted', 'returned', 'approved', 'rejected'
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="notifications")
    recipient = relationship("User", foreign_keys=[user_id], overlaps="notifications,user")
    event = relationship("Event", back_populates="notifications")

    def __init__(self, **kwargs):
        if "recipient_id" in kwargs and "user_id" not in kwargs:
            kwargs["user_id"] = kwargs.pop("recipient_id")
        if "read" in kwargs and "read_at" not in kwargs:
            read_val = kwargs.pop("read")
            kwargs["read_at"] = datetime.utcnow() if read_val else None
        if "kind" not in kwargs:
            kwargs["kind"] = "info"
        super().__init__(**kwargs)

    @property
    def recipient_id(self):
        return self.user_id

    @recipient_id.setter
    def recipient_id(self, val):
        self.user_id = val

    @property
    def read(self):
        return self.read_at is not None

    @read.setter
    def read(self, val):
        if val:
            self.read_at = datetime.utcnow()
        else:
            self.read_at = None


# [SCRUM-23 | Renee] NEW: one row per reassignment - who moved which event from
# whom to whom, and when. A new table rather than new columns on
# CoordinatorAssignmentHistory, because create_all() adds missing tables on Neon
# but never alters existing ones, so new columns would need a migration.
class CoordinatorReassignment(Base):
    __tablename__ = "coordinator_reassignments"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    from_coordinator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_coordinator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reassigned_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reassigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    event = relationship("Event")
    from_coordinator = relationship("User", foreign_keys=[from_coordinator_id])
    to_coordinator = relationship("User", foreign_keys=[to_coordinator_id])
    reassigned_by = relationship("User", foreign_keys=[reassigned_by_id])


# [SCRUM-30 | Sarah] NEW: Venues and Venue Bookings catalogue
class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True, nullable=False)
    location = Column(String(255), nullable=False)
    capacity = Column(Integer, nullable=False)
    facilities = Column(Text, nullable=True)  # JSON-encoded list of strings
    accessibility = Column(Text, nullable=True)
    supported_layouts = Column(Text, nullable=True)  # JSON-encoded list of strings
    operating_hours = Column(String(100), nullable=True)
    setup_time_mins = Column(Integer, default=30, nullable=False)
    turnaround_time_mins = Column(Integer, default=45, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    bookings = relationship("VenueBooking", back_populates="venue", cascade="all, delete-orphan")


class VenueBooking(Base):
    __tablename__ = "venue_bookings"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    booking_start = Column(DateTime, nullable=False)
    booking_end = Column(DateTime, nullable=False)
    status = Column(String(50), default="Pending", nullable=False)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="venue_bookings")
    venue = relationship("Venue", back_populates="bookings")
    # [SCRUM-36 | Renee] The Venue Staff decision on this request, if any
    decision = relationship("VenueBookingDecision", back_populates="booking",
                            uselist=False, cascade="all, delete-orphan")


# [SCRUM-36 | Renee] NEW: one row per Venue Staff decision on a booking request -
# who approved or rejected it, and when. A new table rather than new columns on
# venue_bookings, because create_all() adds missing tables on Neon but never
# alters existing ones (same reason as CoordinatorReassignment, SCRUM-23).
# booking_id is unique, so a request can only ever be decided once.
class VenueBookingDecision(Base):
    __tablename__ = "venue_booking_decisions"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=False, unique=True, index=True)
    decision = Column(String(20), nullable=False)  # "Approved" or "Rejected"
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reason = Column(Text, nullable=True)  # left for SCRUM-37 (rejection reason)

    # Relationships
    booking = relationship("VenueBooking", back_populates="decision")
    decided_by = relationship("User")


# [SCRUM-81 | Elnathan] NEW: one row per hold (Pending booking) whose expiry or
# warning time has been set, or that has had a notice sent. A new table rather
# than new columns on venue_bookings, because create_all() adds missing tables
# on Neon but never alters existing ones. A null expires_at or warning_minutes
# means the default (48 hours after the request, capped at the booking's start;
# a 24-hour warning). booking_id is unique, so a hold has at most one row.
class VenueHold(Base):
    __tablename__ = "venue_holds"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    expires_set_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_set_at = Column(DateTime, nullable=True)
    warning_minutes = Column(Integer, nullable=True)
    warning_sent_at = Column(DateTime, nullable=True)
    expiry_notice_sent_at = Column(DateTime, nullable=True)

    # Relationships. The backref gives booking.hold without changing VenueBooking.
    booking = relationship("VenueBooking", backref=backref("hold", uselist=False, cascade="all, delete-orphan"))
    expires_set_by = relationship("User")
