from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    __table_args__ = (UniqueConstraint("booking_id", "kind", "user_id", name="uq_booking_notification"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True, index=True)
    booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=True)
    booking = relationship("VenueBooking")
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

    requester_id = Column(Integer, ForeignKey("users.id"), nullable=True)

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
    # [SCRUM-89 | Renee] Who made the request, so the review can show their name.
    # A relationship only: requester_id already exists, so the table is unchanged.
    requester = relationship("User", foreign_keys=[requester_id])
    # [SCRUM-36 | Renee] The Venue Staff decision on this request, if any
    decision = relationship("VenueBookingDecision", back_populates="booking",
                            uselist=False, cascade="all, delete-orphan")
    # [SCRUM-80 | Renee] Set when a setup/turnaround change makes this booking clash
    conflict_flag = relationship("VenueBookingConflictFlag", back_populates="booking",
                                 uselist=False, cascade="all, delete-orphan",
                                 foreign_keys="VenueBookingConflictFlag.booking_id")


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


# [SCRUM-80 | Renee] NEW: flags a booking that clashes with another after the
# venue's setup or turnaround time changed (SCRUM-80 AC3 = SCRUM-38 AC8). The
# booking itself is never removed or changed; Venue Staff see the flag and
# decide. A new table rather than a column on venue_bookings, because
# create_all() adds missing tables on Neon but never alters existing ones
# (same reason as VenueBookingDecision). One flag per booking.
class VenueBookingConflictFlag(Base):
    __tablename__ = "venue_booking_conflict_flags"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=False, unique=True, index=True)
    conflicting_booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=False)
    reason = Column(Text, nullable=False)
    flagged_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    booking = relationship("VenueBooking", back_populates="conflict_flag", foreign_keys=[booking_id])


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


# [SCRUM-39 | Melvin] NEW: one row per equipment line item on an event - what
# kind of equipment, how many, and any technical notes. A new table rather than
# new columns on events, because create_all() adds missing tables on Neon but
# never alters existing ones (same reason as VenueBookingDecision, SCRUM-36,
# and VenueHold, SCRUM-81).
#
# The free-text events.equipment_requirements column is deliberately left
# alone. SCRUM-29 treats it as a significant field, so removing or repurposing
# it would break that story. This table is the structured record Technical
# Support work from; the old column stays as the Organiser's own wording.
#
# The unique constraint is a database backstop against an exact duplicate. The
# endpoint additionally refuses a duplicate that differs only in case.
class EquipmentRequest(Base):
    __tablename__ = "equipment_requests"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    equipment_type = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    technical_requirements = Column(Text, nullable=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("event_id", "equipment_type", name="uq_equipment_request_event_type"),
    )

    # Relationships. The backref gives event.equipment_requests without
    # changing the Event class.
    event = relationship(
        "Event",
        backref=backref("equipment_requests", cascade="all, delete-orphan"),
    )
    requested_by = relationship("User")


# [Sprint 5 | SCRUM-40] NEW: one row per status update on an equipment request:
# requested, reserved, arranged or unavailable, with a note and who made it. The
# latest row is the request's status (no rows means requested). A new table
# because create_all() never alters existing ones.
class EquipmentRequestUpdate(Base):
    __tablename__ = "equipment_request_updates"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("equipment_requests.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False)
    note = Column(Text, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    request = relationship("EquipmentRequest", backref=backref("status_updates", cascade="all, delete-orphan"))
    actor = relationship("User")


# [Sprint 5 | SCRUM-87, SCRUM-50] NEW: marks one booking or one equipment request
# of an event for another look, after a safety decision or an approved important
# change. Nothing is cancelled or deleted. A cleared flag stays as history. Exactly
# one of booking_id and equipment_request_id is set. A new table for the same reason.
class ArrangementReviewFlag(Base):
    __tablename__ = "arrangement_review_flags"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("venue_bookings.id"), nullable=True)
    equipment_request_id = Column(Integer, ForeignKey("equipment_requests.id"), nullable=True)
    source = Column(String(30), nullable=False)  # "safety_review" or "important_change"
    reason = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    cleared_at = Column(DateTime, nullable=True)
    cleared_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


# [Sprint 5 | SCRUM-82] NEW: a period when a venue can't be used (maintenance,
# equipment failure, renovation, safety). Availability checks treat it as blocked.
# Times are naive UTC, like bookings. A new table for the same reason.
class VenueUnavailability(Base):
    __tablename__ = "venue_unavailability"

    id = Column(Integer, primary_key=True, index=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False, index=True)
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    venue = relationship("Venue", backref=backref("unavailable_periods", cascade="all, delete-orphan"))
    created_by = relationship("User")

# [Sprint 5] C SCRUM-102 models: insert directly below this line.

# [Sprint 5] D SCRUM-41 models: insert directly below this line.

# [Sprint 5] E SCRUM-45 models: insert directly below this line.

# [Sprint 5] F SCRUM-49 models: insert directly below this line.
from sqlalchemy import Index, text  # [SCRUM-49] Imported here so the shared import block stays free of conflicts.


# [SCRUM-49, SCRUM-50] NEW: an Organiser's request to change an event after
# submission, and the Coordinator's decision. The requested and previous values
# are JSON text (field to value). A new table because create_all() never alters
# existing ones. The partial unique index allows one Pending request per event,
# even if two requests arrive at the same moment.
class EventChangeRequest(Base):
    __tablename__ = "event_change_requests"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_changes = Column(Text, nullable=False)
    previous_values = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="Pending")  # Pending, Approved or Rejected
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decision_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_event_change_request_one_pending",
            "event_id",
            unique=True,
            sqlite_where=text("status = 'Pending'"),
            postgresql_where=text("status = 'Pending'"),
        ),
    )

    # Relationships
    event = relationship("Event", backref=backref("change_requests", cascade="all, delete-orphan"))
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])
