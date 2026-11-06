const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

import {
  NAVIGATION,
  canAccess as roleCanAccess,
  defaultPathForRole,
  normalizeRole as normalizeRoleName,
  requestedFrontendPath,
  resolveRolePath,
} from "./src/rbac.js";
import { beginAuthentication, finishAuthentication, resetSignedOutState } from "./src/session-state.js";
import { NO_CHANGES, affectedBookingRows, bufferSummary, changedFields, createPayload, emptyVenueForm, flaggedBookingRows, formErrorsFrom, validateVenueForm, venueFormFrom } from "./src/venue-record.js"; // [SCRUM-92]

// [SCRUM-94] The fields PATCH /events/{id} accepts (backend ROUTINE_FIELDS, SCRUM-28).
const ROUTINE_FIELDS = ["title", "purpose", "description", "accessibility_needs", "registration_needs"];
// One time zone for every date and time shown or typed: the backend's Singapore time
// (booking_rules.SINGAPORE, SCRUM-95: "messages write booking times in Singapore time,
// as the users read them"). Booking, hold and record times arrive as naive UTC; event
// dates arrive as the naive Singapore wall time the Organiser typed.
const DISPLAY_TIME_ZONE = "Asia/Singapore";
const DISPLAY_OFFSET = "+08:00";
const DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-SG", { timeZone: DISPLAY_TIME_ZONE, day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true });
const TIME_FORMAT = new Intl.DateTimeFormat("en-SG", { timeZone: DISPLAY_TIME_ZONE, hour: "numeric", minute: "2-digit", hour12: true });
const DAY_KEY_FORMAT = new Intl.DateTimeFormat("en-CA", { timeZone: DISPLAY_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" });
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

// One badge tone per backend status (event statuses: backend/status.py; booking
// statuses: booking_rules and venues). The label is always the status itself.
const STATUS_TONES = {
  Draft: "neutral", Submitted: "waiting", "Under Review": "info", Returned: "attention",
  Approved: "success", Rejected: "danger", Planning: "info", "Safety Review": "waiting", Confirmed: "success",
  Completed: "neutral", Cancelled: "muted", Pending: "waiting", Tentative: "waiting", Expired: "muted",
};

// The amend fields GET /events/{id} returns but the GET /events list does not.
// clarification_note is shown on its own, after the details, by clarificationNote().
const EVENT_DETAIL_HIDDEN = ["id", "status", "organiser_id", "assigned_coordinator_id", "clarification_note"];
// The request form's labels for every field POST /events/{id}/amend accepts (events.EventAmend), so the
// automatic amendment note can name them the way the Organiser saw them.
const EVENT_FIELD_LABELS = {
  title: "Event title", purpose: "Purpose", description: "Description", proposed_start: "Proposed start",
  expected_attendance: "Expected attendance", venue_requirements: "Venue", accessibility_needs: "Accessibility",
  equipment_requirements: "Equipment", registration_needs: "Registration and check-in",
};
// Temporary (BACKEND_ISSUES.md #15): the API returns the automatic amendment note in the same field as the
// Coordinator's question, with no type, so it's recognised by the text events.amend_event writes.
const AMENDMENT_NOTE = /^Request amended by organiser\.\s*(?:Fields changed:\s*(.*?)\.?|No field values changed\.?)\s*$/;
const AMEND_DETAIL_FIELDS = ["venue_requirements", "accessibility_needs", "equipment_requirements", "registration_needs"];

// Organiser request sections: one per status (backend/status.py), the ones that need the
// Organiser first. The section heading is the request's status, so rows carry no badge.
// Descriptions follow events.py: submit (Draft, Returned), amend (Submitted, Under Review,
// Returned), routine edits from View details (PLANNING_STATUSES: Approved, Planning, Confirmed).
// Submitted and Under Review stay apart: a Submitted request may not have a Coordinator yet
// (the Lead assigns one), while Under Review means the assigned Coordinator started the review.
const ORGANISER_GROUPS = [
  { key: "returned", label: "Returned for clarification", description: "A Coordinator asked a question. Edit the request, then resubmit it.", statuses: ["Returned"], attention: true },
  { key: "draft", label: "Drafts", description: "Not sent yet. Submit a draft when it's ready for review.", statuses: ["Draft"] },
  { key: "submitted", label: "Submitted", description: "Sent for review. The review hasn't started yet, and you can still amend it.", statuses: ["Submitted"] },
  { key: "under-review", label: "Under review", description: "A Coordinator is reviewing it. You can still amend it.", statuses: ["Under Review"] },
  { key: "approved", label: "Approved", description: "You can update routine details from View details.", statuses: ["Approved"] },
  { key: "planning", label: "Planning", description: "You can update routine details from View details.", statuses: ["Planning"] },
  { key: "safety-review", label: "Safety review", description: "The Safety Officer is checking the arrangements.", statuses: ["Safety Review"] },
  { key: "confirmed", label: "Confirmed", description: "You can update routine details from View details.", statuses: ["Confirmed"] },
  { key: "rejected", label: "Rejected", description: "A rejected request can't be amended.", statuses: ["Rejected"] },
  { key: "completed", label: "Completed", description: "No further changes can be made.", statuses: ["Completed"] },
  { key: "cancelled", label: "Cancelled", description: "No further changes can be made.", statuses: ["Cancelled"] },
];
// The desktop sidebar's collapsed state, kept in this browser between page loads.
// With no saved choice the sidebar starts as a rail on narrow screens (under 1024px).
const SIDEBAR_KEY = "connectsphere.sidebarCollapsed";
const COMPACT_NAV_QUERY = "(max-width: 767px)";
function savedSidebarCollapsed() {
  try {
    const saved = globalThis.localStorage?.getItem(SIDEBAR_KEY);
    if (saved === "true" || saved === "false") return saved === "true";
  } catch { /* storage blocked: use the default */ }
  return typeof window.innerWidth === "number" && window.innerWidth < 1024;
}

// Coordinator queue sections: one per status, active work first. Actions come from events.py:
// start review (Submitted), approve / request clarification / reject (Submitted, Under Review),
// routine edits from View details (PLANNING_STATUSES). Closed statuses have no decisions left.
// Venue bookings are offered in every status: the backend accepts them in any (BACKEND_ISSUES #5).
const COORDINATOR_GROUPS = [
  { key: "submitted", label: "Awaiting first review", description: "Not reviewed yet, longest wait first.", statuses: ["Submitted"], active: true, attention: true },
  { key: "under-review", label: "Under review", description: "You started the review. Record a decision from Record decision.", statuses: ["Under Review"], active: true },
  { key: "returned", label: "Waiting on the Organiser", description: "You asked for clarification. The Organiser can edit and resubmit it.", statuses: ["Returned"], active: true },
  { key: "approved", label: "Approved", description: "You can update routine details from View details.", statuses: ["Approved"], active: true },
  { key: "planning", label: "Planning", description: "You can update routine details from View details.", statuses: ["Planning"], active: true },
  { key: "safety-review", label: "Safety review", description: "The Safety Officer is checking the arrangements.", statuses: ["Safety Review"], active: true },
  { key: "confirmed", label: "Confirmed", description: "You can update routine details from View details.", statuses: ["Confirmed"], active: true },
  { key: "rejected", label: "Rejected", description: "No further decisions can be made.", statuses: ["Rejected"], active: false },
  { key: "completed", label: "Completed", description: "No further decisions can be made.", statuses: ["Completed"], active: false },
  { key: "cancelled", label: "Cancelled", description: "No further decisions can be made.", statuses: ["Cancelled"], active: false },
];
const COORDINATOR_OTHER = { key: "other", label: "Other statuses", description: "Each request shows its status below its title.", statuses: [], active: true };
const listView = () => ({ loaded: false, error: "", denied: false, query: "" });

// Sort comparators shared by every list (UI_DECISIONS "List controls"). Missing values go last.
const byAsc = field => (a, b) => (a[field] ? 0 : 1) - (b[field] ? 0 : 1) || String(a[field] || "").localeCompare(String(b[field] || ""));
const byDesc = field => (a, b) => (a[field] ? 0 : 1) - (b[field] ? 0 : 1) || String(b[field] || "").localeCompare(String(a[field] || ""));
const keepOrder = () => 0;
// Technical Support worklist sections, by when the event happens (Singapore days). A line's status and
// reserve action are its own component (src/equipment-reservations.js); sections go by the event date.
const TECHNICAL_WINDOW_DAYS = 7;
const TECHNICAL_GROUPS = [
  { key: "soon", label: "Today and the next 7 days", description: "Prepare these first.", attention: true },
  { key: "later", label: "Later", description: "Events more than 7 days away." },
  { key: "undated", label: "Date not set", description: "The event has no proposed date yet, so it can't be scheduled." },
  { key: "past", label: "Past events", description: "Events before today, kept for reference." },
];
// Event statuses in workflow order, for status filter chips
const EVENT_STATUS_ORDER = ["Submitted", "Under Review", "Returned", "Approved", "Planning", "Safety Review", "Confirmed", "Rejected", "Completed", "Cancelled"];
const BOOKING_FILTERS = ["All", "Pending", "Confirmed", "Rejected", "Expired", "Cancelled"];
// Venue Staff booking sections, in the order they need attention. Pending is the only status Venue Staff can
// decide (venues._decide_booking), whatever its date; Confirmed bookings that haven't ended still hold the
// venue; everything else is the record.
const VENUE_BOOKING_GROUPS = [
  { key: "decide", title: "Needs a decision", hint: "Pending requests. Approve one, or reject it with a reason. A hold that runs out turns the request Expired, and it can't be decided." },
  { key: "upcoming", title: "Upcoming confirmed", hint: "Approved bookings that haven't ended. They hold the venue for their date and time." },
  { key: "closed", title: "Past and closed", hint: "Rejected, expired, cancelled and ended bookings, for reference. Nothing here needs action." },
];
// Statuses that hold a venue: what retiring a venue lists as affected (venues.retire_venue).
const BLOCKING_BOOKING_STATUSES = ["Pending", "Confirmed"];

// Requests without the date (e.g. a Draft has no submitted date) go after those with one.
const ORGANISER_SORTS = {
  start: byAsc("proposed_start"),
  submitted: byDesc("submitted_at"),
  created: byDesc("created_at"),
};

document.addEventListener("alpine:init", () => {
  Alpine.data("appState", () => ({
    user: null,
    currentPath: "/login",
    sidebarOpen: false,
    sidebarCollapsed: savedSidebarCollapsed(),
    compactNav: !!window.matchMedia?.(COMPACT_NAV_QUERY).matches,
    loading: false,
    pendingPath: requestedFrontendPath(window.location.hash),
    accessDenied: false,
    unbuiltRole: false,
    events: [],
    eventsView: { loaded: false, error: "", denied: false, query: "", group: "all", sort: "start", busyId: null },
    draftBusy: false,
    assignedEvents: [],
    allCoordinatorEvents: [],
    assignedView: { ...listView(), scope: "all", sort: "queue" },
    allEventsView: { ...listView(), status: "all", sort: "submitted" },
    leadView: { query: "", filter: "all", sort: "attention", loaded: false, error: "", denied: false, coordinatorsError: "" },
    assignmentState: {},
    picker: { eventId: null, query: "", active: 0 },
    bookingView: { query: "", sort: "start" },
    technicalView: { query: "", sort: "event" },
    leadEvents: [],
    coordinators: [],
    notifications: [],
    notificationOpen: false,
    bookingDialog: null,
    venueWorkspace: null,
    venueCalendar: null, // [SCRUM-96] the venue availability calendar dialog
    staffBooking: null,
    technicalWorklist: null,
    eventDetail: null,
    decisionBusy: false,
    alert: { message: "", kind: "error" },
    fieldErrors: {},
    loginForm: { username: "", password: "" },
    showPassword: false,
    draft: {
      title: "",
      purpose: "",
      description: "",
      proposed_start: "",
      expected_attendance: null,
      venue_requirements: "",
      accessibility_needs: "",
      equipment_requirements: "",
      registration_needs: "",
    },
    amendForm: {
      title: "",
      purpose: "",
      description: "",
      proposed_start: "",
      expected_attendance: null,
      venue_requirements: "",
      accessibility_needs: "",
      equipment_requirements: "",
      registration_needs: "",
    },
    amendOriginal: null,
    decisionForm: { reason: "" },
    modal: { open: false, kind: "", event: null },
    navigation: NAVIGATION,

    async init() {
      if (window.location.hash) {
        window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
      }
      // Below 768px the sidebar is an overlay; leaving that width closes it.
      const compact = window.matchMedia?.(COMPACT_NAV_QUERY);
      compact?.addEventListener?.("change", event => {
        this.compactNav = event.matches;
        if (!event.matches) this.sidebarOpen = false;
      });
      await this.loadSession();
      window.setInterval(() => this.refreshWorkspace(), 15000);
    },

    // One control: on phones it opens and closes the overlay; wider, it collapses the sidebar to a rail.
    sidebarExpanded() { return this.compactNav ? this.sidebarOpen : !this.sidebarCollapsed; },
    toggleSidebar() {
      if (this.compactNav) {
        this.sidebarOpen = !this.sidebarOpen;
        // Move focus into the overlay, which sits before the top bar in tab order.
        // Each role sees only its own items, so take the first one that is shown.
        if (this.sidebarOpen && typeof this.$nextTick === "function") this.$nextTick(() => [...(document.querySelectorAll?.("#app-sidebar .nav-item") || [])].find(item => item.offsetParent)?.focus());
        return;
      }
      this.sidebarCollapsed = !this.sidebarCollapsed;
      try { globalThis.localStorage?.setItem(SIDEBAR_KEY, String(this.sidebarCollapsed)); } catch { /* not saved; still applies now */ }
    },

    normalizeRole(role) {
      return normalizeRoleName(role);
    },

    async apiRequest(path, options = {}) {
      // [SCRUM-89] inlineErrors: the caller shows the error itself (e.g. in a
      // dialog), so it isn't sent to the page alert. A 401 still signs out.
      const { inlineErrors = false, deferErrors = false, ...fetchOptions } = options;
      const recipient = this.user;
      const request = { ...fetchOptions, credentials: "include", headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } };
      if (request.body && typeof request.body !== "string") request.body = JSON.stringify(request.body);
      const suppressErrors = inlineErrors || deferErrors;
      const handleError = (status, data) => { if (this.user !== recipient) return; if (!suppressErrors || status === 401) this.handleHttpError(status, data, path); };
      let response;
      try {
        response = await fetch(`${API_BASE}${path}`, request);
      } catch {
        const data = { detail: "Cannot reach the backend service. Start FastAPI on port 8000 and try again." };
        handleError(0, data);
        return { ok: false, status: 0, data };
      }
      const data = await response.json().catch(() => ({}));
      if (response.ok) return { ok: true, status: response.status, data };
      handleError(response.status, data);
      return { ok: false, status: response.status, data };
    },

    handleHttpError(status, data, path) {
      if (status === 0 || status === 503) {
        this.showAlert(data.detail || "Cannot reach the backend service. Start FastAPI on port 8000 and try again.");
        return;
      }
      if (status === 401) {
        resetSignedOutState(this);
        this.events = [];
        this.assignedEvents = [];
        this.currentPath = "/login";
        if (path === "/auth/me") this.alert = null;
        else this.showAlert(data.detail || "Not authenticated");
        return;
      }
      if (status === 403) {
        this.showAlert("You don't have permission to perform this action.");
        return;
      }
      if (status === 422) {
        this.fieldErrors = this.parseValidationErrors(data.detail);
        return;
      }
      if (status === 400 && (path === "/events" || (typeof path === "string" && path.endsWith("/amend"))) && typeof data?.detail === "string") {
        if (data.detail.toLowerCase().includes("start time")) {
          this.fieldErrors = { ...this.fieldErrors, proposed_start: data.detail };
          return;
        }
        if (data.detail.toLowerCase().includes("attendance")) {
          this.fieldErrors = { ...this.fieldErrors, expected_attendance: data.detail };
          return;
        }
      }
      const detail = typeof data?.detail === "string" ? this.formatServerTimes(data.detail) : data?.detail;
      this.showAlert(detail || `Request to ${path} failed with status ${status}.`);
    },

    // [SCRUM-89] The message a dialog shows for a failed request: the API's
    // detail (a string, or FastAPI's list of validation errors), or a fallback.
    dialogErrorMessage(response) {
      const detail = response?.data?.detail;
      if (typeof detail === "string" && detail.trim()) return detail;
      if (Array.isArray(detail) && detail.length) return detail.map(error => String(error.msg ?? "").replace(/^Value error, /, "")).filter(Boolean).join(" ") || "The request was not valid.";
      if (response?.status === 403) return "You don't have permission to perform this action.";
      return `The request failed${response?.status ? ` (status ${response.status})` : ""}. Please try again.`;
    },

    parseValidationErrors(detail) {
      if (!Array.isArray(detail)) return {};
      // Pydantic prefixes custom validator messages with "Value error, ".
      return Object.fromEntries(detail.map(error => [error.loc?.at(-1) || "form", String(error.msg ?? "").replace(/^Value error, /, "")]));
    },

    // Success shows as a toast that clears itself; errors and info stay until dismissed.
    showAlert(message, kind = "error") {
      const alert = { message, kind };
      this.alert = alert;
      if (kind === "success" && typeof setTimeout === "function") {
        setTimeout(() => { if (this.alert?.message === alert.message && this.alert?.kind === "success") this.clearAlert(); }, 6000);
      }
    },
    clearAlert() { this.alert = { message: "", kind: "error" }; },

    async loadSession() {
      const response = await this.apiRequest("/auth/me");
      if (!response.ok) {
        if (response.status === 401) this.alert = null;
        return;
      }
      this.user = { ...response.data, role: this.normalizeRole(response.data.role) };
      await this.fetchNotifications();
      const destination = this.pendingPath || this.defaultPath();
      this.pendingPath = null;
      this.navigate(destination);
    },

    async login(username, password) {
      if (!beginAuthentication(this)) return;
      this.fieldErrors = {};
      const response = await this.apiRequest("/auth/login", { method: "POST", body: { username, password } });
      finishAuthentication(this);
      if (!response.ok) return;
      this.user = { user_id: response.data.user_id, username: response.data.username, role: this.normalizeRole(response.data.role) };
      this.loginForm.password = "";
      this.showPassword = false;
      await this.fetchNotifications();
      const destination = this.pendingPath || this.defaultPath();
      this.pendingPath = null;
      this.navigate(destination);
    },

    async logout() {
      this.loading = true;
      const response = await this.apiRequest("/auth/logout", { method: "POST" });
      this.loading = false;
      if (!response.ok) return;
      resetSignedOutState(this);
    },

    defaultPath() { return defaultPathForRole(this.user?.role); },
    rolePath(role) { return defaultPathForRole(role); },
    canAccess(targetRole, userRole) { return roleCanAccess(targetRole, userRole); },
    pageTitle() { if (this.accessDenied) return "Access denied"; return this.navigation.find(entry => entry.path === this.currentPath)?.label || "Workspace"; },
    // One initials rule for avatars and monograms: the first letters of the first two
    // words, or the first two letters of a single word ("organiser1" -> "OR").
    initials(name) {
      const words = String(name || "").split(/[^A-Za-z]+/).filter(Boolean);
      if (!words.length) return "?";
      return (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
    },
    userInitials() { return this.initials(this.user?.username); },
    unreadNotifications() { return this.notifications.filter(notification => !notification.read).length; },
    notificationTitle(notification) {
      return {
        booking_submitted: "New booking request",
        booking_approved: "Booking approved",
        booking_rejected: "Booking rejected",
        hold_warning: "Hold expiry warning",
        hold_expired: "Hold expired",
        hold_expiry_set: "Hold expiry updated",
        submitted: "Event request submitted",
        returned: "Clarification requested",
        approved: "Event request approved",
        rejected: "Event request rejected",
        assigned_to_you: "Event assigned to you",
        reassigned_to_you: "Event reassigned to you",
        reassigned_away: "Event reassigned",
        coordinator_changed: "Coordinator changed",
        coordinator_assigned: "Coordinator assigned",
        equipment_request_updated: "Equipment request updated",
      }[notification?.kind] || "Workspace update";
    },
    notificationTone(notification) {
      if (["booking_approved", "approved"].includes(notification?.kind)) return "success";
      if (["booking_rejected", "rejected", "returned", "hold_expired"].includes(notification?.kind)) return "warning";
      if (["booking_submitted", "assigned_to_you", "reassigned_to_you", "hold_warning"].includes(notification?.kind)) return "action";
      return "info";
    },
    notificationIcon(notification) {
      return this.notificationTone(notification) === "success" ? "check" : this.notificationTone(notification) === "warning" ? "alert" : notification?.booking_id ? "building" : "message";
    },
    // created_at is the backend's naive UTC (datetime.utcnow), like booking times.
    formatNotificationTime(value) { return this.parseServerTime(value, "utc") ? this.formatDateTime(value, "utc") : "Recently"; },
    formatNotificationMessage(notification) { return this.formatServerTimes(notification?.message); },
    // [SCRUM-95] Times in backend messages: marked "UTC", Z or an offset are converted;
    // unmarked times are already Singapore time. "D 10:00 - 12:00" ends that day or the next.
    formatServerTimes(text) {
      return String(text || "").replace(
        /(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?(?: - (\d{2}:\d{2}))?( UTC)?/g,
        (match, day, time, zone, endTime, utc) => {
          const suffix = zone || (utc ? "Z" : DISPLAY_OFFSET);
          const start = new Date(`${day}T${time}${suffix}`);
          if (Number.isNaN(start.getTime())) return match;
          if (!endTime) return this.formatDateTime(start);
          let end = new Date(`${day}T${endTime}${suffix}`);
          if (end <= start) end = new Date(end.getTime() + 24 * 60 * 60 * 1000);
          return this.formatRange(start, end);
        },
      );
    },

    // ---- The one date and time format: "4 Nov 2026, 11:55 am", in Singapore time ----
    // kind "utc": a naive value is UTC (bookings, holds, created_at).
    // kind "local": a naive value is Singapore wall time (event dates, datetime-local inputs).
    // A value with Z or an offset is exact either way.
    parseServerTime(value, kind = "local") {
      if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
      if (!value) return null;
      const text = String(value);
      const date = new Date(HAS_ZONE.test(text) ? text : `${text}${kind === "utc" ? "Z" : DISPLAY_OFFSET}`);
      return Number.isNaN(date.getTime()) ? null : date;
    },
    formatDateTime(value, kind = "local") {
      const date = this.parseServerTime(value, kind);
      return date ? DATE_TIME_FORMAT.format(date) : "Not set";
    },
    formatRange(start, end) {
      if (!start || !end) return `${start ? this.formatDateTime(start) : "Not set"} – ${end ? this.formatDateTime(end) : "Not set"}`;
      const sameDay = DAY_KEY_FORMAT.format(start) === DAY_KEY_FORMAT.format(end);
      return `${this.formatDateTime(start)} – ${sameDay ? TIME_FORMAT.format(end) : this.formatDateTime(end)}`;
    },
    // A datetime-local value typed in Singapore time, as the instant it means.
    inputToDate(value) { return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(String(value || "")) ? this.parseServerTime(value, "local") : null; },
    utcTimestamp(value) { return value && !HAS_ZONE.test(value) ? `${value}Z` : value; },
    parseUtc(value) { return this.parseServerTime(value, "utc"); },
    formatHoldExpiry(value) { return this.formatDateTime(value, "utc"); },
    formatUtc(value) { return this.formatDateTime(value, "utc"); },
    // [SCRUM-95] Booking times are stored in UTC (D7), like hold times.
    formatSchedule(start, end) { return this.formatRange(this.parseUtc(start), this.parseUtc(end)); },
    // [SCRUM-89] AC5: the requester by name.
    requesterLabel(detail) { return detail?.requester_name || "Not recorded"; },
    listText(items) { return Array.isArray(items) && items.length ? items.join(", ") : "Not recorded"; },
    filteredVenueBookings() {
      const workspace = this.venueWorkspace;
      if (!workspace) return [];
      const view = this.bookingView || { query: "", sort: "start" };
      const text = (view.query || "").trim().toLowerCase();
      return workspace.bookings
        .filter(booking => workspace.bookingFilter === "All" || booking.status === workspace.bookingFilter)
        .filter(booking => !text || [`#${booking.id}`, booking.event_title, booking.venue_name].some(value => String(value || "").toLowerCase().includes(text)))
        .sort(view.sort === "hold" ? byAsc("hold_expires_at") : byAsc("booking_start"));
    },
    bookingEnded(booking) { const end = this.parseUtc(booking?.booking_end); return !!end && end <= new Date(); },
    venueBookingGroupOf(booking) {
      if (booking.status === "Pending") return "decide";
      return booking.status === "Confirmed" && !this.bookingEnded(booking) ? "upcoming" : "closed";
    },
    // Search, the status chip and the sort apply first; the sections then split what's left.
    venueBookingGroups() {
      const bookings = this.filteredVenueBookings();
      return VENUE_BOOKING_GROUPS
        .map(group => ({ ...group, bookings: bookings.filter(booking => this.venueBookingGroupOf(booking) === group.key) }))
        .filter(group => group.bookings.length);
    },
    bookingRowState(booking) { return this.venueWorkspace?.rowDecisions?.[booking.id] || {}; },
    // The venue's bookings that still hold it, from the list already loaded: what retiring it affects.
    retireImpact(venue) {
      return (this.venueWorkspace?.bookings || [])
        .filter(booking => booking.venue_id === venue.id && BLOCKING_BOOKING_STATUSES.includes(booking.status) && !this.bookingEnded(booking))
        .sort(byAsc("booking_start"));
    },
    retireImpactKnown(venue) { return !(this.venueWorkspace?.failedVenueIds || []).includes(venue.id); },
    bookingFilterCount(filter) {
      if (!this.venueWorkspace) return 0;
      return filter === "All"
        ? this.venueWorkspace.bookings.length
        : this.venueWorkspace.bookings.filter(booking => booking.status === filter).length;
    },
    iconFor(name) {
      const icons = {
        calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4m8-4v4M3 10h18"/></svg>',
        plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg>',
        inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16l2 9v5H2v-5l2-9Z"/><path d="M2 14h5l2 3h6l2-3h5"/></svg>',
        building: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 21V4h11v17M15 9h5v12M8 8h3m-3 4h3m-3 4h3M2 21h20"/></svg>',
        equipment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.5 6.5 17.5 3l3.5 3.5-3.5 3.5M13 8l3 3-9 9H4v-3l9-9Z"/><path d="m12 4 8 8"/></svg>',
        ticket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8a3 3 0 0 0 0 6v3h18v-3a3 3 0 0 0 0-6V5H3v3Z"/><path d="M13 5v2m0 3v2m0 3v2"/></svg>',
        people: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m1-15a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/></svg>',
        message: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 15a3 3 0 0 1-3 3H9l-5 3v-6a3 3 0 0 1-1-2V7a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3v8Z"/></svg>',
        logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5m5-4 4-4-4-4m4 4H9"/></svg>',
        bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>',
        check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12 4 4L19 6"/></svg>',
        alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 2 20h20L12 3Z"/><path d="M12 9v5m0 3h.01"/></svg>',
        "panel-left": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/></svg>',
        menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
      };
      return icons[name] || icons.calendar;
    },

    navigate(path) {
      this.closeEventDetail();
      const resolution = resolveRolePath(path, this.user?.role);
      this.accessDenied = resolution.accessDenied;
      this.unbuiltRole = resolution.unbuiltRole;
      this.currentPath = resolution.currentPath;
      this.clearAlert();
      if (!this.accessDenied && path === "/events") this.fetchEvents();
      if (!this.accessDenied && path === "/coordinator/events") this.fetchAssignedEvents();
      if (!this.accessDenied && path === "/coordinator/all-events") this.fetchAllCoordinatorEvents();
      if (!this.accessDenied && path === "/lead/events") this.fetchLeadData();
      if (!this.accessDenied && path === "/venue") this.fetchVenueWorkspace();
      if (!this.accessDenied && path === "/technical") this.fetchTechnicalWorklist();
    },

    async fetchEvents() {
      const recipient = this.user;
      const view = this.eventsView;
      this.loading = true;
      const response = await this.apiRequest("/events", { inlineErrors: true });
      if (this.user !== recipient) return;
      this.loading = false;
      if (response.ok) {
        this.events = response.data;
        Object.assign(view, { loaded: true, error: "", denied: false });
      } else if (response.status !== 401) {
        // A failed refresh keeps the list already shown and says so above it.
        Object.assign(view, { denied: response.status === 403, error: this.dialogErrorMessage(response) });
      }
    },

    // Organiser list: search, filter and sort the requests GET /events already returned.
    organiserMatches(event) {
      const query = (this.eventsView.query || "").trim().toLowerCase();
      if (!query) return true;
      return [`#${event.id}`, event.title, event.purpose, event.description].some(text => String(text || "").toLowerCase().includes(query));
    },
    organiserGroupOf(event) { return ORGANISER_GROUPS.find(group => group.statuses.includes(event.status))?.key || "other"; },
    organiserGroups() {
      const sort = ORGANISER_SORTS[this.eventsView.sort] || ORGANISER_SORTS.start;
      const groups = [...ORGANISER_GROUPS, { key: "other", label: "Other statuses", description: "Each request shows its status below its title.", statuses: [] }];
      return groups
        .filter(group => this.eventsView.group === "all" || this.eventsView.group === group.key)
        .map(group => ({ ...group, events: this.events.filter(event => this.organiserGroupOf(event) === group.key && this.organiserMatches(event)).sort(sort) }))
        .filter(group => group.events.length);
    },
    // A chip for each section that has requests, counting what it contains before search.
    organiserFilterChips() {
      const chips = [{ key: "all", label: "All", count: this.events.length }];
      for (const group of [...ORGANISER_GROUPS, { key: "other", label: "Other statuses" }]) {
        const count = this.events.filter(event => this.organiserGroupOf(event) === group.key).length;
        if (count) chips.push({ key: group.key, label: group.label, count });
      }
      return chips;
    },
    organiserShownCount() { return this.organiserGroups().reduce((total, group) => total + group.events.length, 0); },
    clearOrganiserFilters() { Object.assign(this.eventsView, { query: "", group: "all" }); },
    // GET /events has no rejection reason (BACKEND_ISSUES.md #10). The Organiser's
    // "rejected" notification carries it, in the wording notify_organiser_of_status uses.
    rejectionReason(event) {
      const notice = this.notifications.filter(item => item.event_id === event.id && item.kind === "rejected").sort((a, b) => b.id - a.id)[0];
      const message = notice?.message || "";
      const marker = " Reason: ";
      const at = message.indexOf(marker, Math.max(0, message.indexOf("has been rejected.")));
      return at >= 0 ? message.slice(at + marker.length).trim() : "";
    },
    // Focus the first field the backend flagged, once Alpine has drawn the error.
    focusFirstError(container, fallback = null) {
      const focus = () => (document.querySelector?.(`${container} [aria-invalid="true"]`) || (fallback && document.querySelector?.(`${container} ${fallback}`)))?.focus();
      if (typeof this.$nextTick === "function") this.$nextTick(focus);
    },

    async createEventDraft(data, { submit = false } = {}) {
      if (this.draftBusy) return;
      this.fieldErrors = {};
      const payload = {
        title: data.title ?? "",
        purpose: data.purpose ?? "",
        description: data.description ?? "",
        proposed_start: data.proposed_start ? data.proposed_start : null,
        expected_attendance: data.expected_attendance !== null && data.expected_attendance !== "" && !isNaN(Number(data.expected_attendance)) ? Number(data.expected_attendance) : null,
        venue_requirements: data.venue_requirements ?? "",
        accessibility_needs: data.accessibility_needs ?? "",
        equipment_requirements: data.equipment_requirements ?? "",
        registration_needs: data.registration_needs ?? "",
      };
      this.draftBusy = true;
      let response;
      try {
        response = await this.apiRequest("/events", { method: "POST", body: payload });
      } finally {
        this.draftBusy = false;
      }
      if (!response.ok) { this.focusFirstError(".request-form"); return; }
      {
        this.draft = {
          title: "",
          purpose: "",
          description: "",
          proposed_start: "",
          expected_attendance: null,
          venue_requirements: "",
          accessibility_needs: "",
          equipment_requirements: "",
          registration_needs: "",
        };
        if (!submit) {
          this.navigate("/events");
          this.showAlert("Your event request was saved as a draft.", "success");
          return;
        }
        this.draftBusy = true;
        let submitted;
        try {
          submitted = await this.apiRequest(`/events/${response.data.id}/submit`, { method: "POST", deferErrors: true });
        } finally {
          this.draftBusy = false;
        }
        this.navigate("/events");
        if (submitted.ok) this.showAlert("Your event request was submitted for review.", "success");
        else if (submitted.status !== 401) this.showAlert(`Saved as a draft, but it wasn't submitted: ${this.dialogErrorMessage(submitted)}`);
      }
    },

    async submitEvent(eventId) {
      if (this.eventsView.busyId) return;
      this.eventsView.busyId = eventId;
      let response;
      try {
        response = await this.apiRequest(`/events/${eventId}/submit`, { method: "POST" });
      } finally {
        this.eventsView.busyId = null;
      }
      if (response.ok) { await this.fetchEvents(); this.showAlert("Event request submitted for review.", "success"); }
    },

    // Only the fields that differ from what was loaded, so an unchanged field is never sent.
    amendPayload(data) {
      const original = this.amendOriginal || (this.modal?.event ? {
        title: (this.modal.event.title || "").trim(),
        purpose: this.modal.event.purpose || "",
        description: this.modal.event.description || "",
        proposed_start: this.modal.event.proposed_start ? this.modal.event.proposed_start.slice(0, 16) : "",
        expected_attendance: this.modal.event.expected_attendance ?? null,
        venue_requirements: this.modal.event.venue_requirements || "",
        accessibility_needs: this.modal.event.accessibility_needs || "",
        equipment_requirements: this.modal.event.equipment_requirements || "",
        registration_needs: this.modal.event.registration_needs || "",
      } : null);

      const payload = {};
      if (original) {
        if (data.title !== undefined && (data.title || "").trim() !== original.title) {
          payload.title = (data.title || "").trim();
        }
        if (data.purpose !== undefined && (data.purpose || "") !== original.purpose) {
          payload.purpose = data.purpose;
        }
        if (data.description !== undefined && (data.description || "") !== original.description) {
          payload.description = data.description;
        }
        if (data.proposed_start !== undefined) {
          const currentStart = data.proposed_start || null;
          const origStart = original.proposed_start || null;
          if (currentStart !== origStart) {
            payload.proposed_start = currentStart;
          }
        }
        if (data.expected_attendance !== undefined) {
          const currentAtt = (data.expected_attendance !== null && data.expected_attendance !== "" && !isNaN(Number(data.expected_attendance)))
            ? Number(data.expected_attendance) : null;
          const origAtt = (original.expected_attendance !== null && original.expected_attendance !== "" && !isNaN(Number(original.expected_attendance)))
            ? Number(original.expected_attendance) : null;
          if (currentAtt !== origAtt) {
            payload.expected_attendance = currentAtt;
          }
        }
        if (data.venue_requirements !== undefined && (data.venue_requirements || "") !== original.venue_requirements) {
          payload.venue_requirements = data.venue_requirements;
        }
        if (data.accessibility_needs !== undefined && (data.accessibility_needs || "") !== original.accessibility_needs) {
          payload.accessibility_needs = data.accessibility_needs;
        }
        if (data.equipment_requirements !== undefined && (data.equipment_requirements || "") !== original.equipment_requirements) {
          payload.equipment_requirements = data.equipment_requirements;
        }
        if (data.registration_needs !== undefined && (data.registration_needs || "") !== original.registration_needs) {
          payload.registration_needs = data.registration_needs;
        }
      } else {
        for (const [k, v] of Object.entries(data)) {
          if (v !== undefined) payload[k] = v;
        }
      }
      return payload;
    },
    amendChangeCount() { return this.modal?.kind === "amend" && this.amendOriginal ? Object.keys(this.amendPayload(this.amendForm)).length : 0; },

    // Save (POST /amend) keeps the changes and the status: a Returned request stays Returned.
    // Resubmit saves any changes first, then POST /submit sends a Returned request back to Submitted
    // for its Coordinator. With no changes it's only submitted (the backend allows that).
    async amendEvent(eventId, data, { resubmit = false } = {}) {
      this.fieldErrors = {};
      const payload = this.amendPayload(data);
      const changed = Object.keys(payload).length > 0;
      if (!changed && !resubmit) {
        this.showAlert("No changes to save.", "info");
        return;
      }
      this.decisionBusy = true;
      // Which button is running, for its "Saving…" or "Resubmitting…" label
      const modal = this.modal;
      modal.action = resubmit ? "resubmit" : "save";
      try {
        if (changed) {
          const response = await this.apiRequest(`/events/${eventId}/amend`, { method: "POST", body: payload });
          if (!response.ok) { this.focusFirstError(".amend-dialog"); return; }
          // Saved: later diffs start from the values just stored.
          this.amendOriginal = { ...this.amendOriginal, ...this.amendFormFrom({ ...this.amendOriginal, ...payload }) };
        }
        if (resubmit) {
          const submitted = await this.apiRequest(`/events/${eventId}/submit`, { method: "POST", deferErrors: true });
          if (!submitted.ok) {
            if (submitted.status !== 401) this.showAlert(`${changed ? "Your changes were saved, but the request wasn't resubmitted" : "The request wasn't resubmitted"}: ${this.dialogErrorMessage(submitted)}`);
            await this.fetchEvents();
            return;
          }
        }
        this.closeModal();
        await this.fetchEvents();
        this.showAlert(resubmit ? "Request resubmitted" : "Changes saved", "success");
      } finally {
        this.decisionBusy = false;
        if (modal) modal.action = "";
      }
    },

    async fetchAssignedEvents() {
      const recipient = this.user;
      this.loading = true;
      const response = await this.apiRequest("/coordinator/events/data", { inlineErrors: true });
      if (this.user !== recipient) return;
      this.loading = false;
      if (response.ok) this.assignedEvents = response.data;
      this.recordListResult(this.assignedView, response);
    },
    async fetchAllCoordinatorEvents() {
      const recipient = this.user;
      const response = await this.apiRequest("/coordinator/events/all-data", { inlineErrors: true });
      if (this.user !== recipient) return;
      if (response.ok) this.allCoordinatorEvents = response.data;
      this.recordListResult(this.allEventsView, response);
    },
    // A failed refresh keeps the list already shown and says so above it; 401 signs out instead.
    recordListResult(view, response) {
      if (response.ok) Object.assign(view, { loaded: true, error: "", denied: false });
      else if (response.status !== 401) Object.assign(view, { denied: response.status === 403, error: this.dialogErrorMessage(response) });
    },

    // Coordinator queue: sections per status from the loaded list, in the backend's queue order
    // (awaiting first review longest wait first, then by proposed date).
    coordinatorMatches(event, query) {
      const text = (query || "").trim().toLowerCase();
      return !text || [`#${event.id}`, event.title, event.organiser, event.assigned_coordinator].some(value => String(value || "").toLowerCase().includes(text));
    },
    coordinatorGroupOf(event) { return COORDINATOR_GROUPS.find(group => group.statuses.includes(event.status)) || COORDINATOR_OTHER; },
    coordinatorInScope(event, scope) { return scope === "all" || this.coordinatorGroupOf(event).active === (scope === "active"); },
    // Sections that need a decision come first in All and Active; sorting applies within each section.
    coordinatorGroups() {
      const view = this.assignedView;
      const sort = view.sort === "start" ? byAsc("proposed_start") : keepOrder;
      return [...COORDINATOR_GROUPS, COORDINATOR_OTHER]
        .filter(group => view.scope === "all" || group.active === (view.scope === "active"))
        .map(group => ({ ...group, events: this.assignedEvents.filter(event => this.coordinatorGroupOf(event).key === group.key && this.coordinatorMatches(event, view.query)).sort(sort) }))
        .filter(group => group.events.length);
    },
    coordinatorScopeChips() {
      const count = scope => this.assignedEvents.filter(event => this.coordinatorInScope(event, scope)).length;
      return [{ key: "all", label: "All", count: count("all") }, { key: "active", label: "Active", count: count("active") }, { key: "completed", label: "Completed", count: count("completed") }];
    },
    coordinatorShownCount() { return this.coordinatorGroups().reduce((total, group) => total + group.events.length, 0); },
    // The row's one main action, by status. Decisions themselves are made in the detail dialog.
    coordinatorPrimary(event) {
      if (!this.canDecideEvent(event)) return null;
      return event.status === "Submitted" ? { label: "Start review", run: () => this.startReview(event.id) } : { label: "Record decision", run: () => this.openEventDetail(event.id) };
    },
    // All events (read-only): search and a status filter over the loaded list.
    // Status chips in workflow order, only for statuses present; unknown statuses follow.
    allEventStatusChips() {
      const present = [...new Set(this.allCoordinatorEvents.map(event => event.status))];
      const statuses = [...EVENT_STATUS_ORDER.filter(status => present.includes(status)), ...present.filter(status => !EVENT_STATUS_ORDER.includes(status))];
      return [{ key: "all", label: "All", count: this.allCoordinatorEvents.length }, ...statuses.map(status => ({ key: status, label: status, count: this.allCoordinatorEvents.filter(event => event.status === status).length }))];
    },
    filteredAllEvents() {
      const view = this.allEventsView;
      return this.allCoordinatorEvents.filter(event => (view.status === "all" || event.status === view.status) && this.coordinatorMatches(event, view.query))
        .sort(view.sort === "start" ? byAsc("proposed_start") : keepOrder);
    },

    // Lead: needs assignment (no Coordinator yet) or assigned, in the backend's order (oldest submission first).
    leadFilterChips() {
      const count = filter => this.leadEvents.filter(event => this.leadInFilter(event, filter)).length;
      return [{ key: "all", label: "All", count: count("all") }, { key: "unassigned", label: "Needs assignment", count: count("unassigned") }, { key: "assigned", label: "Assigned", count: count("assigned") }];
    },
    leadInFilter(event, filter) { return filter === "all" || (filter === "unassigned") === !event.assigned_coordinator_id; },
    filteredLeadEvents() {
      const view = this.leadView;
      const sorts = { attention: (a, b) => this.leadAttentionRank(a) - this.leadAttentionRank(b), start: byAsc("proposed_start"), submitted: keepOrder };
      return this.leadEvents.filter(event => this.leadInFilter(event, view.filter) && this.coordinatorMatches(event, view.query))
        .sort(sorts[view.sort] || keepOrder);
    },

    // Technical Support: events grouped as before; search covers the event and each line, and
    // sorting orders the events (and the lines within each).
    technicalLineMatches(item, query) {
      const text = (query || "").trim().toLowerCase();
      return !text || [`#${item.event_id}`, item.event_title, item.equipment_type, item.technical_requirements, item.requested_by].some(value => String(value || "").toLowerCase().includes(text));
    },

    // ---- Shared list controls (UI_DECISIONS "List controls"): one config per list, one markup pattern.
    listControls(key) {
      const configs = {
        organiser: () => ({ view: this.eventsView, filter: { target: this.eventsView, key: "group" }, chips: this.organiserFilterChips(), placeholder: "Title, purpose, description or #ID", label: "Show requests by status",
          sorts: [{ value: "start", label: "Earliest proposed date" }, { value: "submitted", label: "Recently submitted" }, { value: "created", label: "Recently created" }], sortLabel: "Sort by", shown: this.organiserShownCount(), noun: "requests" }),
        assigned: () => ({ view: this.assignedView, filter: { target: this.assignedView, key: "scope" }, chips: this.coordinatorScopeChips(), placeholder: "Title, organiser or #ID", label: "Show requests",
          sorts: [{ value: "queue", label: "Longest waiting" }, { value: "start", label: "Earliest proposed date" }], sortLabel: "Sort by", shown: this.coordinatorShownCount(), noun: "requests" }),
        allEvents: () => ({ view: this.allEventsView, filter: { target: this.allEventsView, key: "status" }, chips: this.allEventStatusChips(), placeholder: "Title, organiser, coordinator or #ID", label: "Show events by status",
          sorts: [{ value: "submitted", label: "Earliest submitted" }, { value: "start", label: "Earliest proposed date" }], sortLabel: "Sort by", shown: this.filteredAllEvents().length, noun: "events" }),
        lead: () => ({ view: this.leadView, filter: { target: this.leadView, key: "filter" }, chips: this.leadFilterChips(), placeholder: "Title, organiser, coordinator or #ID", label: "Show requests by assignment",
          sorts: [{ value: "attention", label: "Needs attention" }, { value: "submitted", label: "Earliest submitted" }, { value: "start", label: "Earliest proposed date" }], sortLabel: "Sort by", shown: this.filteredLeadEvents().length, noun: "requests" }),
        bookings: () => this.venueWorkspace && ({ view: this.bookingView, filter: { target: this.venueWorkspace, key: "bookingFilter" }, chips: BOOKING_FILTERS.map(filter => ({ key: filter, label: filter, count: this.bookingFilterCount(filter) })),
          placeholder: "Event, venue or booking #ID", label: "Filter booking requests", sorts: [{ value: "start", label: "Earliest booking date" }, { value: "hold", label: "Soonest hold expiry" }], sortLabel: "Sort by", shown: this.filteredVenueBookings().length, noun: "bookings" }),
        technical: () => ({ view: this.technicalView, filter: null, chips: null, placeholder: "Event, equipment, requirements, recorded by or #ID", label: "",
          sorts: [{ value: "event", label: "Earliest event date" }, { value: "recorded", label: "Recently recorded" }], sortLabel: "Sort by", shown: this.technicalEventGroups().reduce((total, group) => total + group.items.length, 0), noun: "equipment lines" }),
      };
      const config = configs[key]?.();
      if (!config) return null;
      const selected = config.filter ? config.chips.find(chip => chip.key === config.filter.target[config.filter.key]) : null;
      const scope = selected && !["all", "All"].includes(selected.key) ? ` in ${selected.label}` : "";
      const query = (config.view.query || "").trim();
      return { ...config, id: key, selected, query,
        announce: query ? `${config.shown} ${config.shown === 1 ? config.noun.replace(/s$/, "").replace(/ lines$/, " line") : config.noun} match${config.shown === 1 ? "es" : ""}${scope}` : "",
        noMatch: `No ${config.noun} match “${query}”${scope}` };
    },
    clearListSearch(key) {
      const config = this.listControls(key);
      if (config) config.view.query = "";
      if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.(`${key}-search`)?.focus());
    },

    async fetchLeadData() {
      const recipient = this.user;
      this.loading = true;
      const [eventsResponse, coordinatorsResponse] = await Promise.all([
        this.apiRequest("/lead/events", { inlineErrors: true }),
        this.apiRequest("/lead/coordinators", { inlineErrors: true }),
      ]);
      if (this.user !== recipient) return;
      this.loading = false;
      if (eventsResponse.ok) {
        // A choice not saved yet is kept when the list refreshes, unless the request's assignment changed meanwhile.
        const previous = new Map(this.leadEvents.map(event => [event.id, event]));
        this.leadEvents = eventsResponse.data.map(event => {
          const before = previous.get(event.id);
          const unsaved = before && before.assigned_coordinator_id === event.assigned_coordinator_id && this.assignmentChanged(before);
          return { ...event, selectedCoordinatorId: unsaved ? before.selectedCoordinatorId : (event.assigned_coordinator_id || "") };
        });
      }
      this.recordListResult(this.leadView, eventsResponse);
      if (coordinatorsResponse.ok) this.coordinators = coordinatorsResponse.data;
      this.leadView.coordinatorsError = coordinatorsResponse.ok || coordinatorsResponse.status === 401 ? "" : this.dialogErrorMessage(coordinatorsResponse);
    },

    async fetchNotifications() {
      const recipient = this.user;
      const response = await this.apiRequest("/notifications");
      if (response.ok && this.user === recipient) this.notifications = response.data;
    },

    async refreshWorkspace() {
      if (!this.user || this.loading) return;
      await this.fetchNotifications();
      if (this.currentPath === "/coordinator/events") await this.fetchAssignedEvents();
      if (this.currentPath === "/coordinator/all-events") await this.fetchAllCoordinatorEvents();
      if (this.currentPath === "/lead/events") await this.fetchLeadData();
      if (this.currentPath === "/venue") await this.fetchVenueWorkspace();
      if (this.currentPath === "/technical") await this.fetchTechnicalWorklist();
    },

    // GET /equipment-requests (backend equipment.py, SCRUM-39 AC2): the worklist
    // across every event, soonest event first. Each line's status and reserve action
    // are in src/equipment-reservations.js (SCRUM-40, SCRUM-44).
    async fetchTechnicalWorklist() {
      if (this.normalizeRole(this.user?.role) !== "Technical Support") return;
      if (!this.technicalWorklist) this.technicalWorklist = { items: [], busy: false, loaded: false, error: "", denied: false };
      const worklist = this.technicalWorklist;
      if (worklist.busy) return;
      worklist.busy = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest("/equipment-requests", { inlineErrors: true });
        if (this.user !== recipient || this.technicalWorklist !== worklist) return;
        if (response.ok && Array.isArray(response.data?.equipment_requests)) {
          worklist.items = response.data.equipment_requests;
          this.recordListResult(worklist, response);
        } else {
          // A failed refresh keeps the worklist shown and says so; a 403 shows the permission state.
          this.recordListResult(worklist, response.ok ? { ok: false, status: 200, data: { detail: "The equipment worklist returned an unexpected response." } } : response);
        }
      } finally {
        if (this.technicalWorklist === worklist) worklist.busy = false;
      }
    },

    // Which section an event belongs to, by its Singapore day compared with today's.
    technicalWhen(start) {
      const date = this.parseServerTime(start, "local");
      if (!date) return "undated";
      const day = DAY_KEY_FORMAT.format(date);
      const today = DAY_KEY_FORMAT.format(new Date());
      if (day < today) return "past";
      return day <= DAY_KEY_FORMAT.format(new Date(Date.now() + TECHNICAL_WINDOW_DAYS * 86400e3)) ? "soon" : "later";
    },
    // "Today", "Tomorrow" or "In 5 days", for events in the first section.
    technicalDaysAway(event) {
      const date = this.parseServerTime(event?.event_proposed_start, "local");
      if (!date) return "";
      const days = Math.round((Date.parse(DAY_KEY_FORMAT.format(date)) - Date.parse(DAY_KEY_FORMAT.format(new Date()))) / 86400e3);
      return days <= 0 ? "Today" : days === 1 ? "Tomorrow" : `In ${days} days`;
    },
    // Sections in a fixed order; the sort orders the events (and their lines) within each.
    technicalSections() {
      const events = this.technicalEventGroups();
      return TECHNICAL_GROUPS
        .map(group => ({ ...group, events: events.filter(event => this.technicalWhen(event.event_proposed_start) === group.key) }))
        .filter(group => group.events.length);
    },
    technicalLineCount(events) { return events.reduce((total, event) => total + event.items.length, 0); },

    // The worklist's lines grouped by event, keeping the backend's order.
    technicalEventGroups() {
      const view = this.technicalView || { query: "", sort: "event" };
      const groups = [];
      for (const item of this.technicalWorklist?.items || []) {
        if (!this.technicalLineMatches(item, view.query)) continue;
        let group = groups.find(entry => entry.event_id === item.event_id);
        if (!group) groups.push(group = { event_id: item.event_id, event_title: item.event_title, event_proposed_start: item.event_proposed_start, items: [] });
        group.items.push(item);
      }
      if (view.sort === "recorded") {
        for (const group of groups) {
          group.items.sort(byDesc("created_at"));
          group.latest = group.items[0]?.created_at || "";
        }
        groups.sort(byDesc("latest"));
      }
      return groups;
    },

    isVenueStaff() { return this.normalizeRole(this.user?.role) === "Venue Staff"; },

    async fetchVenueWorkspace() {
      if (!this.isVenueStaff()) return;
      if (!this.venueWorkspace) this.venueWorkspace = {
        venues: [], bookings: [], busy: false, loaded: false, bookingError: false,
        // The venue list's failure (error, denied) and the venues whose bookings didn't load
        error: "", denied: false, failedVenueIds: [], rowDecisions: {},
        bookingFilter: "All", showCreate: false, saving: false, form: emptyVenueForm(),
        // [SCRUM-92] the venue being edited, the form's message, a pending retire, and the last save's result
        editing: null, formMessage: null, retire: null, outcome: null,
      };
      const workspace = this.venueWorkspace;
      // [SCRUM-101] A refresh already running may have read the list before a save or
      // retire, so a request made meanwhile runs once that one ends instead of being lost.
      if (workspace.busy) { workspace.refreshAgain = true; return; }
      workspace.busy = true;
      workspace.refreshAgain = false;
      const recipient = this.user;
      try {
        const venues = await this.apiRequest("/venues", { inlineErrors: true });
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        // A failed refresh keeps what's shown and says so; a first failure shows the error state.
        if (!venues.ok || !Array.isArray(venues.data)) {
          if (venues.ok) Object.assign(venues, { ok: false, data: { detail: "The venue list came back in an unexpected format." } });
          this.recordListResult(workspace, venues);
          return;
        }
        this.recordListResult(workspace, venues);
        workspace.venues = venues.data;
        const results = await Promise.all(venues.data.map(async venue => {
          const response = await this.apiRequest(`/venues/${venue.id}/bookings`, { inlineErrors: true });
          return { venue, response };
        }));
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        const failed = results.filter(({ response }) => !response.ok || !Array.isArray(response.data));
        workspace.bookingError = failed.length > 0;
        workspace.failedVenueIds = failed.map(({ venue }) => venue.id);
        workspace.bookings = results.flatMap(({ venue, response }) => response.ok && Array.isArray(response.data)
          ? response.data.map(booking => ({ ...booking, venue_name: venue.name })) : []);
      } finally {
        if (this.venueWorkspace === workspace) workspace.busy = false;
        // In finally, so a failed older list still leads to the later refresh.
        if (workspace.refreshAgain && this.user === recipient && this.venueWorkspace === workspace) await this.fetchVenueWorkspace();
      }
    },

    async createVenue() {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || this.venueWriteBusy() || workspace.busy) return;
      // [SCRUM-92] all nine fields (AC1), checked against the backend's rules first (D4)
      this.fieldErrors = validateVenueForm(workspace.form);
      workspace.formMessage = null;
      if (Object.keys(this.fieldErrors).length) return;
      workspace.saving = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest("/venues", { method: "POST", body: createPayload(workspace.form) });
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        if (!response.ok) { this.showVenueFormErrors(response); return; }
        workspace.form = emptyVenueForm();
        workspace.showCreate = false;
        await this.fetchVenueWorkspace();
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        this.showAlert("Venue added. Coordinators and Leads can now request it.", "success");
      } finally {
        if (this.venueWorkspace === workspace) workspace.saving = false;
      }
    },

    // [SCRUM-92 | Elnathan] Edit and retire a venue; the rules are in src/venue-record.js.
    // Every request re-checks the user and the workspace when it returns, so a
    // reply that arrives after logout changes nothing.
    venueBufferSummary(venue) { return bufferSummary(venue); },

    // One venue write at a time: while a save or a retire is in flight, the
    // other venue actions wait, and their buttons are disabled.
    venueWriteBusy() {
      const workspace = this.venueWorkspace;
      return !!(workspace?.saving || workspace?.retire?.busy);
    },

    showVenueFormErrors(response) {
      const { fields, message } = formErrorsFrom(response);
      this.fieldErrors = fields;
      this.venueWorkspace.formMessage = message ? { kind: "error", text: message } : null;
      this.clearAlert();
    },

    // The buttons wait for a venue write in flight: closing the form mid-save
    // would drop its result. closeVenueForm() itself has no guard, because a
    // good save calls it while saving is still true.
    toggleVenueCreate() {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || this.venueWriteBusy()) return;
      const opening = !workspace.showCreate || !!workspace.editing;
      this.closeVenueForm();
      workspace.showCreate = opening;
    },

    cancelVenueForm() {
      if (!this.venueWriteBusy()) this.closeVenueForm();
    },

    closeVenueForm() {
      const workspace = this.venueWorkspace;
      if (!workspace) return;
      workspace.showCreate = false;
      workspace.editing = null;
      workspace.form = emptyVenueForm();
      workspace.formMessage = null;
      this.fieldErrors = {};
    },

    saveVenueForm() {
      return this.venueWorkspace?.editing ? this.updateVenue() : this.createVenue();
    },

    // AC2: prefill from GET /venues/{id}.
    async openVenueEdit(venue) {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || this.venueWriteBusy()) return;
      const recipient = this.user;
      const response = await this.apiRequest(`/venues/${venue.id}`);
      if (this.user !== recipient || this.venueWorkspace !== workspace) return;
      if (!response.ok) {
        const detail = response.data?.detail;
        this.showAlert(typeof detail === "string" ? detail : "This venue could not be loaded.");
        await this.fetchVenueWorkspace();
        return;
      }
      this.closeVenueForm();
      workspace.editing = response.data;
      workspace.form = venueFormFrom(response.data);
      workspace.showCreate = true;
    },

    // AC2, AC3: send only the changed fields (D3); list newly flagged bookings (D5).
    async updateVenue() {
      const workspace = this.venueWorkspace;
      const venue = workspace?.editing;
      if (!this.isVenueStaff() || !venue || this.venueWriteBusy() || workspace.busy) return;
      this.fieldErrors = validateVenueForm(workspace.form);
      workspace.formMessage = null;
      if (Object.keys(this.fieldErrors).length) return;
      const changes = changedFields(workspace.form, venue);
      if (!Object.keys(changes).length) { workspace.formMessage = { kind: "info", text: NO_CHANGES }; return; }
      workspace.saving = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/${venue.id}`, { method: "PATCH", body: changes });
        if (this.user !== recipient || this.venueWorkspace !== workspace || workspace.editing !== venue) return;
        if (!response.ok) { this.showVenueFormErrors(response); return; }
        workspace.outcome = {
          kind: "updated",
          venueName: response.data.venue?.name || venue.name,
          // D5: the flagged-bookings section is shown only after a buffer change.
          bufferChanged: "setup_time_mins" in changes || "turnaround_time_mins" in changes,
          flagged: flaggedBookingRows(response.data),
        };
        this.closeVenueForm();
        await this.fetchVenueWorkspace();
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        this.showAlert("Venue updated.", "success");
      } finally {
        if (this.venueWorkspace === workspace) workspace.saving = false;
      }
    },

    // AC4, D6: an in-page confirmation, never a browser dialog.
    askRetireVenue(venue) {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || this.venueWriteBusy()) return;
      workspace.retire = { venue, busy: false, error: "" };
    },

    cancelRetireVenue() {
      const workspace = this.venueWorkspace;
      if (workspace && !workspace.retire?.busy) workspace.retire = null;
    },

    async confirmRetireVenue() {
      const workspace = this.venueWorkspace;
      const retire = workspace?.retire;
      if (!this.isVenueStaff() || !retire || this.venueWriteBusy()) return;
      retire.busy = true;
      retire.error = "";
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/${retire.venue.id}/retire`, { method: "POST" });
        if (this.user !== recipient || this.venueWorkspace !== workspace || workspace.retire !== retire) return;
        if (!response.ok) {
          const detail = response.data?.detail;
          retire.error = typeof detail === "string" ? detail : `The venue could not be retired (status ${response.status}).`;
          this.clearAlert();
          return;
        }
        workspace.outcome = { kind: "retired", venueName: retire.venue.name, affected: affectedBookingRows(response.data) };
        workspace.retire = null;
        if (workspace.editing?.id === retire.venue.id) this.closeVenueForm();
        await this.fetchVenueWorkspace();
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        this.showAlert("Venue retired.", "success");
      } finally {
        retire.busy = false;
      }
    },

    dismissVenueOutcome() {
      if (this.venueWorkspace) this.venueWorkspace.outcome = null;
    },

    // The row's one filled button: approve as requested. The backend re-checks the hold and the venue's
    // availability, so a refusal (409) shows on the row and the list reloads to show the current status.
    async approveBookingRow(booking) {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || booking.status !== "Pending" || this.bookingRowState(booking).busy) return;
      workspace.rowDecisions[booking.id] = { busy: true, error: "" };
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${booking.id}/approve`, { method: "POST", inlineErrors: true });
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        if (response.ok) {
          delete workspace.rowDecisions[booking.id];
          await this.fetchVenueWorkspace();
          if (this.user !== recipient || this.venueWorkspace !== workspace) return;
          this.showAlert(`${booking.event_title} confirmed at ${booking.venue_name}.`, "success");
          return;
        }
        if (response.status === 401) return;
        workspace.rowDecisions[booking.id] = { busy: false, error: this.dialogErrorMessage(response) };
        if (response.status === 409) await this.fetchVenueWorkspace();
      } finally {
        const state = this.venueWorkspace === workspace && workspace.rowDecisions[booking.id];
        if (state && state.busy) workspace.rowDecisions[booking.id] = { ...state, busy: false };
      }
    },
    // Rejecting needs a reason, so it opens the review with the reason field focused.
    rejectBookingRow(booking) { return this.openStaffBooking(booking.id, { focusReason: true }); },

    async openStaffBooking(bookingId, { focusReason = false } = {}) {
      if (!this.isVenueStaff()) return;
      this.clearAlert(); this.fieldErrors = {};
      this.staffBooking = { id: bookingId, detail: null, reason: "", busy: true, holdExpiry: "", error: "", focusReason };
      const review = this.staffBooking;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${bookingId}`);
        if (this.user === recipient && this.staffBooking === review && response.ok) review.detail = response.data;
      } finally {
        if (this.staffBooking === review) review.busy = false;
      }
    },

    async decideStaffBooking(action) {
      const review = this.staffBooking;
      if (!this.isVenueStaff() || !review?.detail || review.busy || review.detail.status !== "Pending" || !["approve", "reject"].includes(action)) return;
      this.fieldErrors = {};
      if (action === "reject" && !review.reason.trim()) { this.fieldErrors.reason = "A rejection reason is required"; return; }
      review.busy = true; review.error = "";
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${review.id}/${action}`, {
          method: "POST", inlineErrors: true, ...(action === "reject" ? { body: { reason: review.reason.trim() } } : {}),
        });
        if (this.user !== recipient || this.staffBooking !== review) return;
        if (!response.ok) review.error = this.dialogErrorMessage(response);
        if (response.ok) {
          review.detail = response.data.booking;
          review.reason = "";
          await this.fetchVenueWorkspace();
          if (this.user !== recipient || this.staffBooking !== review) return;
          this.showAlert(action === "approve" ? "Booking approved." : "Booking rejected.", "success");
        } else if (response.status === 409) {
          const current = await this.apiRequest(`/venues/bookings/${review.id}`);
          if (this.user === recipient && this.staffBooking === review && current.ok) review.detail = current.data;
        }
      } finally {
        if (this.staffBooking === review) review.busy = false;
      }
    },

    // [SCRUM-89] AC2: Venue Staff set or extend a Pending hold's expiry.
    async setStaffHold() {
      const review = this.staffBooking;
      if (!this.isVenueStaff() || !review?.detail || review.busy || review.detail.status !== "Pending") return;
      this.fieldErrors = {}; review.error = "";
      // The input is Singapore time, like every time the app shows.
      const expiry = this.inputToDate(review.holdExpiry);
      if (!expiry) { this.fieldErrors.holdExpiry = "Choose the new expiry date and time"; return; }
      if (expiry <= new Date()) { this.fieldErrors.holdExpiry = "The new expiry must be in the future"; return; }
      const start = this.parseUtc(review.detail.booking_start);
      if (start && expiry > start) { this.fieldErrors.holdExpiry = "The new expiry can't be later than the booking's start"; return; }
      review.busy = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${review.id}/hold`, {
          method: "PUT", inlineErrors: true, body: { expires_at: expiry.toISOString() },
        });
        if (this.user !== recipient || this.staffBooking !== review) return;
        if (response.ok) {
          // PUT /hold returns the booking without its venue or decision, so
          // update the hold fields and keep the rest of the review's detail.
          review.detail = { ...review.detail, ...response.data.booking };
          review.holdExpiry = "";
          await this.fetchVenueWorkspace();
          if (this.user !== recipient || this.staffBooking !== review) return;
          this.showAlert("Hold expiry updated.", "success");
        } else if (response.status !== 401) {
          review.error = this.dialogErrorMessage(response);
          if (response.status === 409) {
            const current = await this.apiRequest(`/venues/bookings/${review.id}`);
            if (this.user === recipient && this.staffBooking === review && current.ok) review.detail = current.data;
          }
        }
      } finally {
        if (this.staffBooking === review) review.busy = false;
      }
    },

    async markNotificationRead(notification) {
      const recipient = this.user;
      const response = await this.apiRequest(`/notifications/${notification.id}/read`, { method: "POST" });
      if (this.user === recipient && response.ok) this.notifications = this.notifications.map(item => item.id === notification.id ? response.data : item);
    },

    // The record a notification can open, from its backend links: Venue Staff
    // open the booking; roles that can read GET /events/{id} open the event.
    // Notifications with neither link (e.g. reassigned_away) open nothing.
    notificationTarget(notification) {
      if (notification?.booking_id && this.isVenueStaff()) return { kind: "booking", id: notification.booking_id, label: "Open booking" };
      if (notification?.event_id && ["Organiser", "Coordinator", "Event Coordinator Lead"].includes(this.normalizeRole(this.user?.role))) return { kind: "event", id: notification.event_id, label: "Open event request" };
      return null;
    },

    async openNotification(notification) {
      if (!notification.read) await this.markNotificationRead(notification);
      const target = this.notificationTarget(notification);
      if (!target) return;
      this.notificationOpen = false;
      if (target.kind === "booking") await this.openStaffBooking(target.id);
      else await this.openEventDetail(target.id);
    },

    canBookEvent(event) {
      if (!event) return false;
      const role = this.normalizeRole(this.user?.role);
      if (role === "Event Coordinator Lead") return true;
      if (role === "Coordinator") return this.assignedEvents.some(item => item.id === event.id);
      return false;
    },

    isOwnPendingHold(booking) {
      const dialog = this.bookingDialog;
      return !!dialog && this.normalizeRole(this.user?.role) === "Coordinator"
        && this.canBookEvent(dialog.event) && booking.event_id === dialog.event.id
        && dialog.bookings.includes(booking) && booking.status === "Pending"
        && Number.isInteger(booking.warning_minutes) && booking.warning_minutes >= 0
        && !dialog.warningInvalid[booking.id];
    },

    canUpdateHoldWarning(booking) {
      return this.isOwnPendingHold(booking) && (this.parseUtc(booking.hold_expires_at)?.getTime() ?? 0) > Date.now();
    },

    async updateHoldWarning(booking) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.warningBusy !== null) return;
      if (!this.canUpdateHoldWarning(booking)) {
        // [SCRUM-95] AC2: the form stays on screen past the expiry until a refresh; say why Save did nothing.
        if (this.isOwnPendingHold(booking)) {
          dialog.warningInvalid[booking.id] = true;
          this.showAlert("This hold has expired. Click Refresh to see its status.");
        }
        return;
      }
      this.clearAlert();
      const value = String(dialog.warningInputs[booking.id] ?? "").trim();
      dialog.warningErrors = {};
      if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {
        dialog.warningErrors[booking.id] = "Enter a non-negative whole number of minutes";
        return;
      }
      dialog.warningBusy = booking.id;
      const recipient = this.user;
      const path = `/venues/bookings/${booking.id}/hold/warning`;
      try {
        const response = await this.apiRequest(path, {
          method: "PUT", body: { warning_minutes: Number(value) }, deferErrors: true,
        });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        if (!response.ok) {
          if (response.status === 422) {
            const errors = this.parseValidationErrors(response.data?.detail);
            dialog.warningErrors[booking.id] = errors.warning_minutes || "Enter a valid warning time";
          } else {
            if (response.status === 409 || response.status === 403) dialog.warningInvalid[booking.id] = true;
            this.handleHttpError(response.status, response.data || {}, path);
          }
          return;
        }
        Object.assign(booking, response.data.booking);
        dialog.warningInputs[booking.id] = String(booking.warning_minutes);
        this.showAlert("Warning time updated.", "success");
      } finally {
        if (this.bookingDialog === dialog) dialog.warningBusy = null;
      }
    },

    async openBookings(event) {
      if (!this.canBookEvent(event)) return;
      this.clearAlert();
      this.fieldErrors = {};
      this.bookingDialog = {
        event, venues: [], bookings: [], busy: true, submitting: false, ready: false, bookingsLoaded: false,
        warningInputs: {}, warningErrors: {}, warningInvalid: {}, warningBusy: null,
        cancelAsk: null, cancelBusy: false, cancelError: "", // [SCRUM-83]
        form: { venue_id: "", booking_start: "", booking_end: "" },
        error: "",
        search: {
          start_time: "",
          end_time: "",
          min_capacity: "",
          location: "",
          accessibility: "",
          layout: "",
          facilities: "",
          available_only: false,
        },
        searching: false,
        searchResults: [],
        searchError: "",
        searchMessage: "",
        searchErrors: {},
        checkingSuitability: false,
        suitabilityResults: [],
        suitabilityError: "",
        suitabilityMessage: "",
        noFacilitiesRequired: false,
        // Which results show below the search form: the last search or the last suitability check
        resultsKind: "",
        requestMessage: "",
      };
      const dialog = this.bookingDialog;
      const recipient = this.user;
      try {
        const [venues, bookings] = await Promise.all([
          this.apiRequest("/venues"), this.apiRequest(`/events/${event.id}/bookings`),
        ]);
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        dialog.ready = venues.ok && Array.isArray(venues.data);
        dialog.bookingsLoaded = bookings.ok && Array.isArray(bookings.data);
        if (dialog.ready) dialog.venues = venues.data;
        else if (venues.ok) this.showAlert("Venue service returned an invalid response. Check the frontend API proxy and refresh bookings.");
        if (dialog.bookingsLoaded) {
          dialog.bookings = bookings.data;
          dialog.warningInputs = Object.fromEntries(bookings.data.map(booking => [booking.id, booking.warning_minutes == null ? "" : String(booking.warning_minutes)]));
        }
        else if (bookings.ok) this.showAlert("Booking history returned an invalid response. Refresh bookings to try again.");
      } finally {
        if (this.bookingDialog === dialog) dialog.busy = false;
      }
    },

    // GET /venues/search accepts any mix of filters, but a search with none lists every venue, so one is
    // required here. The backend wants both times or neither, the end after the start, and an integer
    // capacity; "Available only" means nothing without a period (every venue counts as available).
    validateVenueSearch(search) {
      const errors = {};
      const fields = ["start_time", "end_time", "location", "accessibility", "layout", "facilities"];
      const hasCapacity = search.min_capacity !== undefined && search.min_capacity !== null && String(search.min_capacity).trim() !== "";
      if (!hasCapacity && !fields.some(field => String(search[field] || "").trim())) errors.form = "Enter at least one search field.";
      if (hasCapacity && !(Number.isInteger(Number(search.min_capacity)) && Number(search.min_capacity) > 0)) errors.min_capacity = "Enter a whole number of guests, 1 or more";
      const start = search.start_time ? this.inputToDate(search.start_time) : null;
      const end = search.end_time ? this.inputToDate(search.end_time) : null;
      if (search.start_time && !start) errors.start_time = "Enter a valid start date and time";
      if (search.end_time && !end) errors.end_time = "Enter a valid end date and time";
      if (search.start_time && !search.end_time) errors.end_time = "Add an end time, or clear the start time";
      if (search.end_time && !search.start_time) errors.start_time = "Add a start time, or clear the end time";
      if (start && end && end <= start) errors.end_time = "End must be after start";
      if (search.available_only && !(search.start_time && search.end_time)) errors.available_only = "Add a start and end time to show only available venues";
      return errors;
    },
    async searchVenues() {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.searching) return;
      dialog.searchError = "";
      dialog.searchMessage = "";
      dialog.searchErrors = this.validateVenueSearch(dialog.search || {});
      if (Object.keys(dialog.searchErrors).length) {
        this.focusFirstError(".venue-search-form", dialog.searchErrors.form ? "#search-start" : null);
        return;
      }
      dialog.searching = true;
      const recipient = this.user;
      try {
        const search = dialog.search || {};
        const queryParts = [];
        // [SCRUM-95] The inputs hold Singapore times; send them in UTC (D7), as the
        // booking submit does. An unreadable value goes as typed for the API to reject.
        const utc = value => this.inputToDate(value)?.toISOString() ?? value;
        if (search.start_time) queryParts.push(`start_time=${encodeURIComponent(utc(search.start_time))}`);
        if (search.end_time) queryParts.push(`end_time=${encodeURIComponent(utc(search.end_time))}`);
        if (search.min_capacity !== undefined && search.min_capacity !== null && search.min_capacity !== "") {
          queryParts.push(`min_capacity=${encodeURIComponent(String(search.min_capacity))}`);
        }
        if (search.location) queryParts.push(`location=${encodeURIComponent(search.location.trim())}`);
        if (search.accessibility) queryParts.push(`accessibility=${encodeURIComponent(search.accessibility.trim())}`);
        if (search.layout) queryParts.push(`layout=${encodeURIComponent(search.layout.trim())}`);
        if (search.facilities) queryParts.push(`facilities=${encodeURIComponent(search.facilities.trim())}`);
        if (search.available_only) queryParts.push("available_only=true");

        const query = queryParts.join("&");
        const path = query ? `/venues/search?${query}` : "/venues/search";
        const response = await this.apiRequest(path, { inlineErrors: true });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        dialog.resultsKind = "search";
        dialog.suitabilityResults = [];
        dialog.suitabilityError = "";
        dialog.suitabilityMessage = "";
        if (response.ok) {
          dialog.searchResults = Array.isArray(response.data) ? response.data : [];
          if (!dialog.searchResults.length) {
            dialog.searchMessage = "No venues found matching your criteria. Try fewer filters.";
          }
        } else {
          dialog.searchError = this.dialogErrorMessage(response);
          dialog.searchResults = [];
        }
      } finally {
        if (this.bookingDialog === dialog) dialog.searching = false;
      }
    },

    // GET /events/{id}/venue-suitability checks capacity against the event and the facilities given. Left
    // out, facilities are "not stated" and no venue can come out suitable, so they're required here, or
    // "No facilities required", which sends an empty list (backend suitability.parse_required_facilities).
    async checkSuitability(facilities) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.checkingSuitability) return;
      dialog.suitabilityError = "";
      dialog.suitabilityMessage = "";
      const wanted = String(facilities ?? "").trim();
      const noneRequired = !!dialog.noFacilitiesRequired;
      dialog.searchErrors = { ...(dialog.searchErrors || {}), facilities: "" };
      if (!wanted && !noneRequired) {
        dialog.searchErrors = { facilities: "Enter the facilities to check, or tick “No facilities required”" };
        this.focusFirstError(".venue-search-form");
        return;
      }
      dialog.searchErrors = {};
      dialog.suitabilityResults = [];
      dialog.checkingSuitability = true;
      const recipient = this.user;
      try {
        const query = `?facilities=${encodeURIComponent(noneRequired ? "" : wanted)}`;
        const response = await this.apiRequest(`/events/${dialog.event.id}/venue-suitability${query}`, { inlineErrors: true });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        dialog.resultsKind = "suitability";
        dialog.searchResults = [];
        dialog.searchError = "";
        dialog.searchMessage = "";
        if (response.ok) {
          dialog.suitabilityResults = Array.isArray(response.data?.venues)
            ? response.data.venues
            : Array.isArray(response.data)
              ? response.data
              : [];
          if (!dialog.suitabilityResults.length) dialog.suitabilityMessage = "There are no active venues to check.";
          else if (response.data && response.data.expected_attendance == null) dialog.suitabilityMessage = "This event has no expected attendance, so capacity can't be checked.";
        } else {
          dialog.suitabilityError = this.dialogErrorMessage(response);
        }
      } finally {
        if (this.bookingDialog === dialog) dialog.checkingSuitability = false;
      }
    },

    // Only a venue the search found available can be requested from the results.
    canRequestFromSearch(venue) { return !!venue && venue.is_available !== false; },
    selectVenueFromSearch(venue) {
      const dialog = this.bookingDialog;
      if (!dialog || !this.canRequestFromSearch(venue)) return;
      dialog.form.venue_id = String(venue.id);
      if (dialog.search?.start_time) dialog.form.booking_start = dialog.search.start_time;
      if (dialog.search?.end_time) dialog.form.booking_end = dialog.search.end_time;
      if (Array.isArray(dialog.venues) && !dialog.venues.some(v => v.id === venue.id)) {
        dialog.venues.push(venue);
      }
      this.fieldErrors = {};
      dialog.requestMessage = "";
      // Review before submitting: focus the first field still empty, or the submit button.
      if (typeof this.$nextTick === "function") this.$nextTick(() => {
        const form = document.querySelector?.(".request-booking-form");
        const target = ["#booking-venue", "#booking-start", "#booking-end"].map(id => form?.querySelector(id)).find(field => field && !field.value) || form?.querySelector('button[type="submit"]');
        target?.focus();
        form?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      });
    },

    // ---------------------------------------------------------------------
    // [SCRUM-96] Venue availability calendar (GET /venues/{id}/calendar)
    // ---------------------------------------------------------------------

    canViewVenueCalendar() {
      // Matches the backend's require_venue_searcher: an internal planning
      // function, so Organisers and Attendees never reach it (AC4).
      return ["Coordinator", "Event Coordinator Lead", "Venue Staff"].includes(this.normalizeRole(this.user?.role));
    },

    calendarDateValue(date) {
      const pad = number => String(number).padStart(2, "0");
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    },

    async openVenueCalendar(venue) {
      if (!this.canViewVenueCalendar() || !venue) return;
      // AC1: a week from today, which is the span a planner usually wants.
      const first = new Date();
      const last = new Date(first);
      last.setDate(last.getDate() + 6);
      this.venueCalendar = {
        venue,
        from: this.calendarDateValue(first),
        to: this.calendarDateValue(last),
        busy: false,
        error: "",
        data: null,
        days: [],
      };
      await this.fetchVenueCalendar();
    },

    async fetchVenueCalendar() {
      const calendar = this.venueCalendar;
      if (!calendar || calendar.busy) return;
      calendar.busy = true;
      calendar.error = "";
      const recipient = this.user;
      try {
        // D1: the range is not checked here. AC3 asks for the server's own
        // message, so an impossible range is sent and its 422 is shown.
        const query = `from=${encodeURIComponent(calendar.from)}&to=${encodeURIComponent(calendar.to)}`;
        const response = await this.apiRequest(`/venues/${calendar.venue.id}/calendar?${query}`, { inlineErrors: true });
        if (this.user !== recipient || this.venueCalendar !== calendar) return;
        if (!response.ok) {
          // D2: shown in the dialog, and stale days are cleared so nothing
          // on screen contradicts the error.
          calendar.error = this.dialogErrorMessage(response);
          calendar.data = null;
          calendar.days = [];
          return;
        }
        calendar.data = response.data;
        calendar.days = Array.isArray(response.data?.days) ? response.data.days : [];
      } finally {
        if (this.venueCalendar === calendar) calendar.busy = false;
      }
    },

    closeVenueCalendar() { this.venueCalendar = null; },

    // [SCRUM-82] A day with an unavailable period isn't free, even with no bookings.
    calendarDayIsFree(day) { return !(Array.isArray(day?.busy) && day.busy.length) && !day?.unavailable?.length; },

    calendarHours(day) {
      // AC2: the venue's recorded hours, or an honest "not recorded".
      return day?.operating_hours || this.venueCalendar?.data?.operating_hours || "Not recorded";
    },

    calendarDayLabel(date) {
      if (!date) return "Not set";
      const parsed = new Date(`${date}T00:00:00`);
      return Number.isNaN(parsed.getTime())
        ? date
        : new Intl.DateTimeFormat("en", { weekday: "short", day: "numeric", month: "short" }).format(parsed);
    },

    calendarBufferNote() {
      // D3: explains why the occupied window is wider than the booked one.
      const data = this.venueCalendar?.data;
      if (!data) return "";
      return `Occupied times include ${data.setup_time_mins ?? 0} min setup before and ${data.turnaround_time_mins ?? 0} min turnaround after each booking.`;
    },

    closeBookings() { this.bookingDialog = null; this.fieldErrors = {}; },

    // [SCRUM-82] AC6: the flagged booking's times, in Singapore time, ready for the
    // search and the request form below. The Coordinator still chooses the venue.
    startReplacementRequest(booking) {
      const dialog = this.bookingDialog;
      if (!dialog) return;
      const toInput = value => new Date(this.parseUtc(value).getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 16);
      const start = toInput(booking.booking_start);
      const end = toInput(booking.booking_end);
      Object.assign(dialog.form, { booking_start: start, booking_end: end });
      Object.assign(dialog.search, { start_time: start, end_time: end, available_only: true });
      document.getElementById?.("search-start")?.focus();
    },

    // [SCRUM-83] Cancel one booking. Only a booking still holding the venue can be cancelled; the others stay as they are.
    canCancelBooking(booking) {
      const dialog = this.bookingDialog;
      return !!dialog && this.canBookEvent(dialog.event) && BLOCKING_BOOKING_STATUSES.includes(booking.status) && !this.bookingEnded(booking);
    },
    // [SCRUM-83] Attendees see an event's first Confirmed booking as its venue, so the last one going leaves them none.
    cancelLeavesNoVenue(booking) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.event.status !== "Confirmed" || booking.status !== "Confirmed") return false;
      return !dialog.bookings.some(other => other.id !== booking.id && other.status === "Confirmed");
    },
    askCancelBooking(booking) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.cancelBusy) return;
      dialog.cancelAsk = booking.id;
      dialog.cancelError = "";
    },
    keepBooking() { if (this.bookingDialog && !this.bookingDialog.cancelBusy) this.bookingDialog.cancelAsk = null; },
    async confirmCancelBooking(booking) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.cancelBusy || !this.canCancelBooking(booking)) return;
      dialog.cancelBusy = true;
      this.clearAlert();
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${booking.id}/cancel`, { method: "POST", inlineErrors: true });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        if (response.ok) this.showAlert(`Booking at ${booking.venue_name} cancelled. The event's other bookings are unchanged.`, "success");
        else dialog.cancelError = this.dialogErrorMessage(response);
        // Refresh after a refusal too: it usually means the list on screen is out of date.
        const bookings = await this.apiRequest(`/events/${dialog.event.id}/bookings`);
        if (this.user === recipient && this.bookingDialog === dialog && bookings.ok && Array.isArray(bookings.data)) dialog.bookings = bookings.data;
      } finally {
        dialog.cancelBusy = false;
        dialog.cancelAsk = null;
      }
    },

    async submitBooking() {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.busy || !dialog.ready || !this.canBookEvent(dialog.event)) return;
      this.fieldErrors = {};
      this.clearAlert();
      dialog.error = "";
      const { venue_id, booking_start, booking_end } = dialog.form;
      if (!dialog.venues.some(venue => venue.id === Number(venue_id))) this.fieldErrors.venue_id = "Select a venue";
      // The inputs are Singapore time, like every time the app shows.
      const start = this.inputToDate(booking_start);
      const end = this.inputToDate(booking_end);
      if (!booking_start) this.fieldErrors.booking_start = "Enter a start date and time";
      else if (!start) this.fieldErrors.booking_start = "Enter a valid start date and time";
      else if (start <= new Date()) this.fieldErrors.booking_start = "Start must be in the future";
      if (!booking_end) this.fieldErrors.booking_end = "Enter an end date and time";
      else if (!end || !start || end <= start) this.fieldErrors.booking_end = "End must be after start";
      if (Object.keys(this.fieldErrors).length) { this.focusFirstError(".request-booking-form"); return; }
      dialog.requestMessage = "";
      dialog.busy = true;
      dialog.submitting = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/${Number(venue_id)}/bookings`, {
          // [SCRUM-95] Singapore-time inputs, sent in UTC (D7).
          method: "POST", inlineErrors: true, body: { event_id: dialog.event.id, booking_start: start.toISOString(), booking_end: end.toISOString() },
        });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        if (!response.ok) {
          dialog.error = this.dialogErrorMessage(response);
          return;
        }
        const venueName = dialog.venues.find(venue => venue.id === Number(venue_id))?.name || "the venue";
        dialog.form = { venue_id: "", booking_start: "", booking_end: "" };
        dialog.requestMessage = `Request sent for ${venueName}. It's in Booking history as Pending until Venue Staff decide.`;
        this.showAlert("Venue booking request submitted.", "success");
        const bookings = await this.apiRequest(`/events/${dialog.event.id}/bookings`);
        if (this.user === recipient && this.bookingDialog === dialog) {
          dialog.bookingsLoaded = bookings.ok && Array.isArray(bookings.data);
          if (dialog.bookingsLoaded) {
            dialog.bookings = bookings.data;
            dialog.warningInputs = Object.fromEntries(bookings.data.map(booking => [booking.id, booking.warning_minutes == null ? "" : String(booking.warning_minutes)]));
          }
        }
      } finally {
        if (this.bookingDialog === dialog) {
          dialog.busy = false;
          dialog.submitting = false;
        }
      }
    },

    // The statuses POST /events/{id}/assign and /reassign accept (backend lead.py assignable_statuses).
    canAssignEvent(event) { return ["Submitted", "Under Review", "Returned"].includes(event?.status); },

    // Unsaved means a Coordinator is chosen and it isn't the one already assigned (the backend refuses a same-Coordinator reassignment).
    assignmentChanged(event) {
      return !!event?.selectedCoordinatorId && String(event.selectedCoordinatorId) !== String(event.assigned_coordinator_id ?? "");
    },
    rowAssignment(event) { return this.assignmentState[event.id] || {}; },
    async saveAssignment(event) {
      if (!event.selectedCoordinatorId) { this.showAlert("Select a Coordinator first."); return; }
      if (!this.assignmentChanged(event) || this.rowAssignment(event).busy) return;
      const action = event.assigned_coordinator_id ? "reassign" : "assign";
      const name = this.coordinators.find(coordinator => String(coordinator.id) === String(event.selectedCoordinatorId))?.username || "the Coordinator";
      this.assignmentState = { ...this.assignmentState, [event.id]: { busy: true } };
      const response = await this.apiRequest(`/events/${event.id}/${action}`, { method: "POST", body: { coordinator_id: Number(event.selectedCoordinatorId) }, inlineErrors: true });
      if (response.ok) {
        // The backend accepted it: the choice is now the saved value, so the save actions hide even if the refresh fails.
        event.assigned_coordinator_id = Number(event.selectedCoordinatorId);
        event.assigned_coordinator = name;
        this.assignmentState = { ...this.assignmentState, [event.id]: { message: `${action === "assign" ? "Assigned" : "Reassigned"} to ${name}.` } };
        await this.fetchLeadData();
        this.showAlert(`Event request #${event.id} ${action === "assign" ? "assigned" : "reassigned"} to ${name}.`, "success");
      } else {
        this.assignmentState = { ...this.assignmentState, [event.id]: { error: response.status === 401 ? "" : this.dialogErrorMessage(response) } };
      }
      // Saved: the buttons hide and the row may re-sort, so focus goes back to this row's picker.
      // Failed: the change is still unsaved, so focus stays on the save button to try again.
      const target = response.ok ? `picker-${event.id}` : `assign-save-${event.id}`;
      if (typeof this.$nextTick === "function") this.$nextTick(() => (document.getElementById?.(target) || document.getElementById?.(`picker-${event.id}`))?.focus());
    },
    // Cancel puts back the saved Coordinator (or none). Nothing is sent.
    cancelAssignment(event) {
      if (!event || this.rowAssignment(event).busy) return;
      event.selectedCoordinatorId = event.assigned_coordinator_id || "";
      this.assignmentState = { ...this.assignmentState, [event.id]: {} };
      this.closePicker(event, { returnFocus: false });
      if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.(`picker-${event.id}`)?.focus());
    },
    // The save actions show only while there's something to save (or it's being saved).
    assignmentActionsShown(event) { return this.assignmentChanged(event) || !!this.rowAssignment(event).busy; },
    // What needs the Lead first. "Proposed date passed" is shown only while the request still awaits a decision.
    leadNeedsAssignment(event) { return !event.assigned_coordinator_id && this.canAssignEvent(event); },
    leadDatePassed(event) {
      const start = this.parseServerTime(event.proposed_start, "local");
      return ["Submitted", "Under Review", "Returned"].includes(event.status) && !!start && start < new Date();
    },
    leadAttentionRank(event) { return this.leadNeedsAssignment(event) ? 0 : this.leadDatePassed(event) ? 1 : 2; },
    // Coordinators by fewest open requests, then name: for the workload table and the picker.
    coordinatorName(coordinator) { return coordinator?.display_name || coordinator?.username || ""; },
    coordinatorsByWorkload() {
      return this.coordinators
        .map(coordinator => ({ ...coordinator, name: this.coordinatorName(coordinator), open: this.coordinatorOpenCount(coordinator.id) }))
        .sort((a, b) => a.open - b.open || a.name.localeCompare(b.name));
    },
    openRequestsLabel(count) { return `${count} open ${count === 1 ? "request" : "requests"}`; },

    // ---- Coordinator picker (UI_DECISIONS "Coordinator picker"): one open at a time. Choosing only sets
    // the row's pending choice; nothing is saved until Assign / Reassign.
    pickerOpen(event) { return this.picker.eventId === event.id; },
    openPicker(event) {
      this.picker = { eventId: event.id, query: "", active: 0 };
      if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.(`picker-search-${event.id}`)?.focus());
    },
    closePicker(event, { returnFocus = true } = {}) {
      if (!event || this.picker.eventId !== event.id) return;
      this.picker = { eventId: null, query: "", active: 0 };
      if (returnFocus && typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.(`picker-${event.id}`)?.focus());
    },
    pickerOptions() {
      const text = this.picker.query.trim().toLowerCase();
      return this.coordinatorsByWorkload().filter(coordinator => !text || coordinator.name.toLowerCase().includes(text));
    },
    pickerOptionLabel(coordinator) { return `${coordinator.name} (${this.openRequestsLabel(coordinator.open)})`; },
    pickerChoiceLabel(event) {
      const chosen = this.coordinators.find(coordinator => String(coordinator.id) === String(event.selectedCoordinatorId));
      return chosen ? this.coordinatorName(chosen) : "Select coordinator";
    },
    choosePickerOption(event, coordinator) {
      event.selectedCoordinatorId = coordinator.id;
      this.assignmentState = { ...this.assignmentState, [event.id]: {} };
      this.closePicker(event);
    },
    pickerKeydown(event, keyboardEvent) {
      const options = this.pickerOptions();
      const key = keyboardEvent.key;
      if (key === "ArrowDown" || key === "ArrowUp") {
        keyboardEvent.preventDefault();
        if (!options.length) return;
        const step = key === "ArrowDown" ? 1 : -1;
        this.picker.active = (this.picker.active + step + options.length) % options.length;
        if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.(`picker-option-${event.id}-${options[this.picker.active].id}`)?.scrollIntoView?.({ block: "nearest" }));
      } else if (key === "Home" || key === "End") {
        keyboardEvent.preventDefault();
        this.picker.active = key === "Home" ? 0 : Math.max(0, options.length - 1);
      } else if (key === "Enter") {
        keyboardEvent.preventDefault();
        if (options[this.picker.active]) this.choosePickerOption(event, options[this.picker.active]);
      } else if (key === "Escape") {
        keyboardEvent.preventDefault();
        keyboardEvent.stopPropagation();
        this.closePicker(event);
      } else if (key === "Tab") {
        this.closePicker(event, { returnFocus: false });
      }
    },

    // Open requests per Coordinator, counted from the list already loaded (the Lead sees every non-Draft event).
    coordinatorOpenCount(coordinatorId) {
      return this.leadEvents.filter(event => String(event.assigned_coordinator_id) === String(coordinatorId) && this.coordinatorGroupOf(event).active).length;
    },

    // ---- Equipment for one event (backend equipment.py, SCRUM-39): the lines Technical Support work from.
    // Coordinators read any event's lines and Leads every event's; recording is for the assigned Coordinator or
    // a Lead. A line can't be changed or removed once recorded (BACKEND_ISSUES.md #2).
    canViewEquipment() { return ["Coordinator", "Event Coordinator Lead"].includes(this.normalizeRole(this.user?.role)); },
    canRecordEquipment(detail) {
      const role = this.normalizeRole(this.user?.role);
      return role === "Event Coordinator Lead" || (role === "Coordinator" && detail?.assigned_coordinator_id != null && Number(detail.assigned_coordinator_id) === Number(this.user?.user_id));
    },
    emptyEquipmentForm() { return { equipment_type: "", quantity: "", technical_requirements: "" }; },
    async loadEventEquipment() {
      const view = this.eventDetail;
      if (!view || !this.canViewEquipment()) return;
      if (!view.equipment) view.equipment = { items: [], loading: false, loaded: false, error: "", denied: false, form: this.emptyEquipmentForm(), errors: {}, busy: false, load: 0 };
      const equipment = view.equipment;
      // A newer load (Try again, or the reload after adding a line) wins over an older one still in flight.
      const load = (equipment.load || 0) + 1;
      equipment.load = load;
      equipment.loading = true;
      // Retrying after a failed first load: show the loading state, not the old error beside it.
      if (!equipment.loaded) equipment.error = "";
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${view.id}/equipment-requests`, { inlineErrors: true });
        if (this.user !== recipient || this.eventDetail !== view || equipment.load !== load) return;
        if (response.ok && Array.isArray(response.data?.equipment_requests)) {
          equipment.items = response.data.equipment_requests;
          this.recordListResult(equipment, response);
        } else this.recordListResult(equipment, response.ok ? { ok: false, status: 200, data: { detail: "The equipment list came back in an unexpected format." } } : response);
      } finally {
        if (this.eventDetail === view && equipment.load === load) equipment.loading = false;
      }
    },
    // Checked before calling, from the backend's rules (EquipmentRequestCreate, record_equipment_requirement, and
    // the 100-character equipment_type column): a type, a whole quantity above 0, one line per type.
    validateEquipment(form, items) {
      const errors = {};
      const type = String(form.equipment_type ?? "").trim();
      if (!type) errors.equipment_type = "Enter the equipment type";
      else if (type.length > 100) errors.equipment_type = "Keep the equipment type to 100 characters or fewer";
      else {
        const existing = (items || []).find(item => String(item.equipment_type).toLowerCase() === type.toLowerCase());
        if (existing) errors.equipment_type = `${existing.equipment_type} is already recorded (quantity ${existing.quantity}). Lines can't be changed yet.`;
      }
      const quantity = String(form.quantity ?? "").trim();
      if (!quantity) errors.quantity = "Enter how many are needed";
      else if (!/^\d+$/.test(quantity) || Number(quantity) < 1) errors.quantity = "Enter a whole number, 1 or more";
      return errors;
    },
    async addEquipment() {
      const view = this.eventDetail;
      const equipment = view?.equipment;
      if (!equipment || equipment.busy || !this.canRecordEquipment(view.detail)) return;
      equipment.errors = this.validateEquipment(equipment.form, equipment.items);
      if (Object.keys(equipment.errors).length) { this.focusFirstError(".equipment-form"); return; }
      const notes = String(equipment.form.technical_requirements ?? "").trim();
      const body = { equipment_type: equipment.form.equipment_type.trim(), quantity: Number(equipment.form.quantity), ...(notes ? { technical_requirements: notes } : {}) };
      equipment.busy = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${view.id}/equipment-requests`, { method: "POST", body, inlineErrors: true });
        if (this.user !== recipient || this.eventDetail !== view) return;
        if (response.ok) {
          equipment.form = this.emptyEquipmentForm();
          equipment.errors = {};
          await this.loadEventEquipment();
          if (this.user !== recipient || this.eventDetail !== view) return;
          this.showAlert(`${body.quantity} × ${body.equipment_type} added. Technical Support can see it now.`, "success");
          if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById?.("equipment-type")?.focus());
        } else if (response.status !== 401) {
          // A duplicate (409) belongs to the type; field errors (422) to their fields; anything else above the form.
          const message = this.dialogErrorMessage(response);
          const fields = response.status === 422 && Array.isArray(response.data?.detail) ? this.parseValidationErrors(response.data.detail) : {};
          equipment.errors = response.status === 409 ? { equipment_type: message } : Object.keys(fields).length ? fields : { form: message };
          this.focusFirstError(".equipment-form");
        }
      } finally {
        if (this.eventDetail === view) equipment.busy = false;
      }
    },
    closeEventDetail() { this.eventDetail = null; if (this.modal.kind !== "amend") this.closeModal(); },
    async openEventDetail(eventId) {
      // [SCRUM-94] Organisers open their own requests too (GET /events/{id} only shows them their own).
      if (!["Coordinator", "Event Coordinator Lead", "Organiser"].includes(this.normalizeRole(this.user?.role))) return;
      this.closeEventDetail();
      this.eventDetail = { id: eventId, detail: null, loading: true, error: "", actionError: "", routine: null, notice: "", equipment: null }; // [SCRUM-93] equipment stays null until a role entitled to it loads it
      // Read back Alpine's reactive proxy for identity checks and reactive updates.
      const dialog = this.eventDetail;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${eventId}`);
        if (this.user !== recipient || this.eventDetail !== dialog) return;
        if (response.ok) dialog.detail = response.data;
        else dialog.error = response.status === 403 ? "You don't have permission to view this request." : response.status === 404 ? "Event request not found." : "Request details could not be loaded. Please try again.";
        // [SCRUM-93] AC2: the equipment panel belongs to the Coordinator and
        // the Lead. D4: an Organiser opens this same view and must not make a
        // call their role is not entitled to.
        if (response.ok && this.canViewEquipment()) this.loadEventEquipment();
      } finally { if (this.user === recipient && this.eventDetail === dialog) dialog.loading = false; }
    },
    // The id is in the dialog title and the status in its badge; the raw user ids
    // repeat the organiser and coordinator names shown alongside them.
    eventDetailEntries(detail) {
      return Object.entries(detail || {}).filter(([key]) => !EVENT_DETAIL_HIDDEN.includes(key));
    },
    // A field name as the form labels it; an unknown one reads as words ("catering_needs" -> "Catering needs").
    requestFieldLabel(key) {
      const name = String(key || "").trim();
      if (EVENT_FIELD_LABELS[name]) return EVENT_FIELD_LABELS[name];
      const words = name.replace(/[_-]+/g, " ").trim().toLowerCase();
      return words ? words[0].toUpperCase() + words.slice(1) : "Another field";
    },
    listWords(items) { return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`; },
    // One reading of clarification_note for every role and screen (GET /events and GET /events/{id} both fill it
    // from the latest "Returned" history row). Returns null when there's nothing to show.
    //   question: the Coordinator's note from POST /return.
    //   update:   the automatic note POST /amend writes while the request is Returned. It replaces the question
    //             in the API, so the question can't be shown alongside it (BACKEND_ISSUES.md #15).
    clarificationNote(event) {
      if (!event) return null;
      const raw = String(event.clarification_note ?? "").trim();
      const returned = event.status === "Returned";
      if (!raw) return returned ? { kind: "question", heading: "Coordinator's clarification request", text: "No note was included with this request.", detail: "" } : null;
      const match = raw.match(AMENDMENT_NOTE);
      if (!match) return { kind: "question", heading: "Coordinator's clarification request", text: raw, detail: returned ? "" : "Sent when the request was last returned for clarification." };
      const organiser = this.normalizeRole(this.user?.role) === "Organiser";
      const fields = (match[1] || "").split(",").map(field => field.trim()).filter(Boolean).map(field => this.requestFieldLabel(field));
      const who = organiser ? "You" : "Organiser";
      return {
        kind: "update", heading: "Latest update",
        text: fields.length ? `${who} updated ${this.listWords(fields)}` : `${who} saved the request without changing any fields`,
        detail: organiser
          ? "The Coordinator's question can't be shown here after an update. It may still be in your notifications."
          : "The clarification request can't be shown here after the Organiser updates the request.",
      };
    },
    // Request details use the form's labels too (EVENT_FIELD_LABELS), so a field reads the same everywhere.
    eventFieldLabel(key) { return ({ organiser: "Organiser", assigned_coordinator: "Assigned Coordinator" })[key] || this.requestFieldLabel(key); },
    eventFieldValue(key, value) {
      if (value === null || value === undefined || value === "") return "Not provided";
      if (typeof value === "object") return JSON.stringify(value, null, 2);
      return key === "proposed_start" ? this.formatDate(value) : String(value);
    },
    // [SCRUM-94 | Renee] Update routine information during planning (PATCH /events/{id}, SCRUM-28).
    // AC1, AC4: only the event's Organiser or its assigned Coordinator, and only while it is
    // Approved, Planning or Confirmed (the backend's PLANNING_STATUSES).
    canEditRoutine(event) {
      if (!event || !["Approved", "Planning", "Confirmed"].includes(event.status)) return false;
      const role = this.normalizeRole(this.user?.role);
      const me = Number(this.user?.user_id);
      return (role === "Organiser" && Number(event.organiser_id) === me)
        || (role === "Coordinator" && event.assigned_coordinator_id != null && Number(event.assigned_coordinator_id) === me);
    },
    openRoutineEdit() {
      const view = this.eventDetail;
      if (!view?.detail || view.routine?.busy || !this.canEditRoutine(view.detail)) return;
      view.notice = "";
      view.routine = {
        form: Object.fromEntries(ROUTINE_FIELDS.map(field => [field, view.detail[field] ?? ""])),
        busy: false, error: "", message: "",
      };
    },
    cancelRoutineEdit() {
      const view = this.eventDetail;
      if (view?.routine && !view.routine.busy) view.routine = null;
    },
    // PR #42 review: one comparison for saving and for the unsaved-edits check.
    routineChanges(view = this.eventDetail) {
      const changes = {};
      let error = "";
      if (!view?.routine || !view.detail) return { changes, error };
      for (const field of ROUTINE_FIELDS) {
        const next = String(view.routine.form[field] ?? "").trim();
        if (field === "title" && !next) error = "Title cannot be empty";
        if (next !== String(view.detail[field] ?? "").trim()) changes[field] = next || null;
      }
      return { changes, error };
    },
    routineIsDirty() { return Object.keys(this.routineChanges().changes).length > 0; },
    // ✕, the backdrop and Escape: not while a save is in flight, and ask in the
    // view (D6) before discarding unsaved edits. App code still calls closeEventDetail().
    requestCloseEventDetail() {
      const routine = this.eventDetail?.routine;
      if (routine?.busy) return;
      // Closing while a line is being added would hide whether it was saved.
      if (this.eventDetail?.equipment?.busy) return;
      if (routine && this.routineIsDirty()) { routine.confirmDiscard = true; return; }
      this.closeEventDetail();
    },
    keepRoutineEdit() { if (this.eventDetail?.routine) this.eventDetail.routine.confirmDiscard = false; },
    // AC2: send only the routine fields that changed; AC3, AC4: errors stay in the view.
    async saveRoutineEdit() {
      const view = this.eventDetail;
      const routine = view?.routine;
      if (!routine || routine.busy || !this.canEditRoutine(view.detail)) return;
      routine.error = ""; routine.message = ""; routine.confirmDiscard = false;
      const { changes, error } = this.routineChanges(view);
      if (error) { routine.error = error; return; }
      if (!Object.keys(changes).length) { routine.message = "No changes to save."; return; }
      routine.busy = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${view.id}`, { method: "PATCH", body: changes, inlineErrors: true });
        if (this.user !== recipient) return;
        // PR #42 review: a Coordinator's All Events list refreshes too, as in decideEvent.
        const refreshLists = async () => {
          if (this.normalizeRole(this.user?.role) === "Organiser") return this.fetchEvents();
          const tasks = [this.fetchAssignedEvents()];
          if (this.currentPath === "/coordinator/all-events" || this.allCoordinatorEvents?.length) tasks.push(this.fetchAllCoordinatorEvents());
          await Promise.all(tasks);
        };
        // PR #42 review: saved after the app closed the view (e.g. navigation): still refresh the lists.
        if (this.eventDetail !== view || view.routine !== routine) { if (response.ok) await refreshLists(); return; }
        if (!response.ok) { routine.error = this.dialogErrorMessage(response); return; }
        // PATCH returns the shorter event_to_dict (no organiser or coordinator names),
        // so only the routine fields are copied into the view.
        for (const field of ROUTINE_FIELDS) if (field in response.data) view.detail[field] = response.data[field];
        view.routine = null;
        view.notice = "Event details updated.";
        await refreshLists();
      } finally {
        if (view.routine === routine) routine.busy = false;
      }
    },
    canDecideEvent(event, action = "approve") {
      if (this.normalizeRole(this.user?.role) !== "Coordinator" || !event) return false;
      const assigned = Object.hasOwn(event, "assigned_coordinator_id") ? event.assigned_coordinator_id != null && Number(event.assigned_coordinator_id) === Number(this.user.user_id) : this.assignedEvents.some(row => row.id === event.id);
      return assigned && (action === "review" ? event.status === "Submitted" : ["approve", "return", "reject"].includes(action) && ["Submitted", "Under Review"].includes(event.status));
    },
    async decideEvent(eventId, action, reason) {
      const event = this.eventDetail?.id === eventId ? this.eventDetail.detail : this.assignedEvents.find(row => row.id === eventId);
      if (this.decisionBusy || !this.canDecideEvent(event, action)) return;
      if (["return", "reject"].includes(action) && !reason?.trim()) { this.fieldErrors = { reason: "A reason is required" }; return; }
      const recipient = this.user;
      const dialog = this.eventDetail?.id === eventId ? this.eventDetail : null;
      const modal = this.modal;
      this.decisionBusy = true;
      this.fieldErrors = {};
      if (dialog) dialog.actionError = "";
      try {
        const response = await this.apiRequest(`/events/${eventId}/${action}`, { method: "POST", ...(["return", "reject"].includes(action) ? { body: { reason: reason.trim() } } : {}) });
        if (this.user !== recipient) return;
        if (!response.ok) {
          const message = typeof response.data?.detail === "string" ? response.data.detail : "The decision could not be saved. Please check the request and try again.";
          if (dialog && this.eventDetail === dialog) dialog.actionError = message;
          if (this.modal === modal && modal.open) this.fieldErrors = { ...this.fieldErrors, form: message };
        }
        if (response.ok && this.modal === modal && modal.open) this.closeModal();
        if (response.ok || [400, 403, 404].includes(response.status)) {
          const refreshes = [this.fetchAssignedEvents()];
          if (this.currentPath === "/coordinator/all-events" || this.allCoordinatorEvents.length) refreshes.push(this.fetchAllCoordinatorEvents());
          if (dialog && this.eventDetail === dialog) {
            refreshes.push((async () => {
              const current = await this.apiRequest(`/events/${eventId}`);
              if (this.user !== recipient || this.eventDetail !== dialog) return;
              dialog.detail = current.ok ? current.data : null;
              dialog.error = current.ok ? "" : current.status === 404 ? "Event request not found." : current.status === 403 ? "You don't have permission to view this request." : "Request details could not be refreshed. Please try again.";
            })());
          }
          await Promise.all(refreshes);
        }
        if (this.user === recipient && response.ok) this.showAlert(({ review: "Review started.", approve: "Event request approved.", reject: "Event request rejected.", return: "Clarification requested from the organiser." })[action], "success");
      } finally { if (this.user === recipient) this.decisionBusy = false; }
    },
    async startReview(eventId) { return this.decideEvent(eventId, "review"); },
    async handleApprove(eventId) { return this.decideEvent(eventId, "approve"); },
    async handleReject(eventId, reason) { return this.decideEvent(eventId, "reject", reason); },
    async handleReturn(eventId, reason) { return this.decideEvent(eventId, "return", reason); },

    openDecision(event, kind) {
      if (this.decisionBusy || !this.canDecideEvent(event, kind)) return;
      this.fieldErrors = {};
      this.decisionForm = { reason: "" };
      this.modal = { open: true, kind, event };
    },
    amendFormFrom(event) {
      return {
        title: (event.title || "").trim(),
        purpose: event.purpose || "",
        description: event.description || "",
        proposed_start: event.proposed_start ? event.proposed_start.slice(0, 16) : "",
        expected_attendance: event.expected_attendance ?? null,
        venue_requirements: event.venue_requirements || "",
        accessibility_needs: event.accessibility_needs || "",
        equipment_requirements: event.equipment_requirements || "",
        registration_needs: event.registration_needs || "",
      };
    },
    openAmend(event) {
      this.fieldErrors = {};
      this.clearAlert();
      this.amendOriginal = this.amendFormFrom(event);
      this.amendForm = { ...this.amendOriginal };
      this.modal = { open: true, kind: "amend", event, loading: false, loadError: "", resubmit: event.status === "Returned", action: "" };
      // GET /events leaves out the venue, accessibility, equipment and registration
      // fields, so a row from the list is completed from GET /events/{id} first;
      // otherwise those fields would open blank and a save could overwrite them.
      if (!AMEND_DETAIL_FIELDS.every(field => field in event)) this.loadAmendDetail(event.id);
    },
    async loadAmendDetail(eventId) {
      const modal = this.modal;
      modal.loading = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${eventId}`, { inlineErrors: true });
        if (this.user !== recipient || this.modal !== modal) return;
        if (!response.ok) { modal.loadError = this.dialogErrorMessage(response); return; }
        modal.event = response.data;
        this.amendOriginal = this.amendFormFrom(response.data);
        this.amendForm = { ...this.amendOriginal };
      } finally {
        if (this.modal === modal) modal.loading = false;
      }
    },
    closeModal() { this.modal = { open: false, kind: "", event: null }; this.fieldErrors = {}; this.amendOriginal = null; },
    async submitModal() { if (this.decisionBusy || !this.modal.open || this.modal.loading || this.modal.loadError) return; const eventId = this.modal.event.id; if (this.modal.kind === "amend") return this.amendEvent(eventId, this.amendForm, { resubmit: !!this.modal.resubmit }); if (!this.decisionForm.reason.trim()) { this.fieldErrors = { reason: "A reason is required" }; return; } return this.modal.kind === "reject" ? this.handleReject(eventId, this.decisionForm.reason) : this.handleReturn(eventId, this.decisionForm.reason); },
    // One tone per backend status, events and bookings alike (STATUS_TONES).
    statusClass(status) { return `badge-${STATUS_TONES[status] || "neutral"}`; },
    bookingStatusClass(status) { return this.statusClass(status); },
    suitabilityBadgeClass(status) { return { suitable: "badge-success", unsuitable: "badge-danger", unknown: "badge-waiting" }[status] || "badge-neutral"; },
    suitabilityLabel(status) { return { suitable: "Suitable", unsuitable: "Unsuitable", unknown: "Unknown" }[status] || String(status || "Unknown"); },
    availabilityClass(available) { return available ? "badge-success" : "badge-danger"; },
    formatDate(value) { return this.formatDateTime(value, "local"); },
    formatTime(value) { const date = this.parseServerTime(value, "local"); return date ? TIME_FORMAT.format(date) : "Not set"; },
  }));
});
