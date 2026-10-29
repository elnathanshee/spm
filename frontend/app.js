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

document.addEventListener("alpine:init", () => {
  Alpine.data("appState", () => ({
    user: null,
    currentPath: "/login",
    sidebarOpen: false,
    loading: false,
    pendingPath: requestedFrontendPath(window.location.hash),
    accessDenied: false,
    unbuiltRole: false,
    events: [],
    assignedEvents: [],
    allCoordinatorEvents: [],
    leadEvents: [],
    coordinators: [],
    notifications: [],
    notificationOpen: false,
    bookingDialog: null,
    venueWorkspace: null,
    staffBooking: null,
    alert: { message: "", kind: "error" },
    fieldErrors: {},
    loginForm: { username: "", password: "" },
    showPassword: false,
    draft: { title: "", purpose: "", description: "", proposed_start: "", expected_attendance: null, venue_requirements: "" },
    amendForm: { title: "", description: "" },
    decisionForm: { reason: "" },
    modal: { open: false, kind: "", event: null },
    navigation: NAVIGATION,

    async init() {
      if (window.location.hash) {
        window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
      }
      await this.loadSession();
      window.setInterval(() => this.refreshWorkspace(), 15000);
    },

    normalizeRole(role) {
      return normalizeRoleName(role);
    },

    async apiRequest(path, options = {}) {
      const request = { ...options, credentials: "include", headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } };
      if (request.body && typeof request.body !== "string") request.body = JSON.stringify(request.body);
      let response;
      try {
        response = await fetch(`${API_BASE}${path}`, request);
      } catch {
        const data = { detail: "Cannot reach the backend service. Start FastAPI on port 8000 and try again." };
        this.handleHttpError(0, data, path);
        return { ok: false, status: 0, data };
      }
      const data = await response.json().catch(() => ({}));
      if (response.ok) return { ok: true, status: response.status, data };
      this.handleHttpError(response.status, data, path);
      return { ok: false, status: response.status, data };
    },

    handleHttpError(status, data, path) {
      if (status === 0 || status === 503) {
        this.showAlert(data.detail || "Cannot reach the backend service. Start FastAPI on port 8000 and try again.");
        return;
      }
      if (status === 401) {
        this.user = null;
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
      this.showAlert(data.detail || `Request to ${path} failed with status ${status}.`);
    },

    parseValidationErrors(detail) {
      if (!Array.isArray(detail)) return {};
      return Object.fromEntries(detail.map(error => [error.loc?.at(-1) || "form", error.msg]));
    },

    showAlert(message, kind = "error") { this.alert = { message, kind }; },
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
    roleWorkspace() { return { Organiser: "Event requests", Coordinator: "Planning & review", "Event Coordinator Lead": "Assignment oversight", "Venue Staff": "Venue operations", "Technical Support": "Technical operations", Attendee: "My events" }[this.user?.role] || "Event services"; },
    rolePageHeading() { return { "Venue Staff": "Venue operations", "Technical Support": "Technical support", Attendee: "Event directory" }[this.user?.role] || "Your workspace"; },
    rolePageDescription() { return { "Venue Staff": "Review venue-related work assigned to your role.", "Technical Support": "Keep track of technical and equipment arrangements assigned to you.", Attendee: "Find attendee-facing event information available to your role." }[this.user?.role] || "Your role-specific view of ConnectSphere."; },
    roleEmptyMessage() { return { "Venue Staff": "There are no venue requests available right now. Relevant venue work will appear here when supported by the event workflow.", "Technical Support": "There are no technical requirements available right now. Assigned equipment and support work will appear here.", Attendee: "There are no attendee events available right now. Events open to your role will appear here." }[this.user?.role] || "There is no work requiring your attention right now."; },
    roleIcon() { return { "Venue Staff": "building", "Technical Support": "equipment", Attendee: "ticket" }[this.user?.role] || "calendar"; },
    userInitials() { return String(this.user?.username || "U").split(/[._\s-]+/).map(part => part[0]).join("").slice(0, 2).toUpperCase(); },
    unreadNotifications() { return this.notifications.filter(notification => !notification.read).length; },
    notificationTitle(notification) {
      return {
        booking_submitted: "New booking request",
        booking_approved: "Booking approved",
        booking_rejected: "Booking rejected",
        submitted: "Event request submitted",
        returned: "Clarification requested",
        approved: "Event request approved",
        rejected: "Event request rejected",
        assigned_to_you: "Event assigned to you",
        reassigned_to_you: "Event reassigned to you",
        reassigned_away: "Event reassigned",
        coordinator_changed: "Coordinator changed",
        coordinator_assigned: "Coordinator assigned",
      }[notification?.kind] || "Workspace update";
    },
    notificationTone(notification) {
      if (["booking_approved", "approved"].includes(notification?.kind)) return "success";
      if (["booking_rejected", "rejected", "returned"].includes(notification?.kind)) return "warning";
      if (["booking_submitted", "assigned_to_you", "reassigned_to_you"].includes(notification?.kind)) return "action";
      return "info";
    },
    notificationIcon(notification) {
      return this.notificationTone(notification) === "success" ? "check" : this.notificationTone(notification) === "warning" ? "alert" : notification?.booking_id ? "building" : "message";
    },
    formatNotificationTime(value) {
      if (!value) return "Recently";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "Recently";
      return new Intl.DateTimeFormat("en-SG", { dateStyle: "medium", timeStyle: "short" }).format(date);
    },
    formatNotificationMessage(notification) {
      return String(notification?.message || "")
        .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g, value => this.formatDate(value.replace(" ", "T")));
    },
    bookingStatusClass(status) { return { Pending: "status-waiting", Confirmed: "status-approved", Rejected: "status-rejected" }[status] || "status-neutral"; },
    formatSchedule(start, end) { return `${this.formatDate(start)} – ${this.formatDate(end)}`; },
    filteredVenueBookings() {
      const workspace = this.venueWorkspace;
      if (!workspace) return [];
      return workspace.bookingFilter === "All"
        ? workspace.bookings
        : workspace.bookings.filter(booking => booking.status === workspace.bookingFilter);
    },
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
      };
      return icons[name] || icons.calendar;
    },

    navigate(path) {
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
    },

    async fetchEvents() {
      this.loading = true;
      const response = await this.apiRequest("/events");
      if (response.ok) this.events = response.data;
      this.loading = false;
    },

    async createEventDraft(data) {
      this.fieldErrors = {};
      const response = await this.apiRequest("/events", { method: "POST", body: data });
      if (response.ok) { this.navigate("/events"); this.showAlert("Your event request was saved as a draft.", "success"); }
    },

    async submitEvent(eventId) {
      const response = await this.apiRequest(`/events/${eventId}/submit`, { method: "POST" });
      if (response.ok) { await this.fetchEvents(); this.showAlert("Event request submitted for review.", "success"); }
    },

    async amendEvent(eventId, data) {
      this.fieldErrors = {};
      const response = await this.apiRequest(`/events/${eventId}/amend`, { method: "POST", body: data });
      if (response.ok) { this.closeModal(); await this.fetchEvents(); this.showAlert("Event request updated.", "success"); }
    },

    async fetchAssignedEvents() {
      this.loading = true;
      const response = await this.apiRequest("/coordinator/events/data");
      if (response.ok) this.assignedEvents = response.data;
      this.loading = false;
    },
    async fetchAllCoordinatorEvents() {
      const response = await this.apiRequest("/coordinator/events/all-data");
      if (response.ok) this.allCoordinatorEvents = response.data;
    },

    async fetchLeadData() {
      this.loading = true;
      const [eventsResponse, coordinatorsResponse] = await Promise.all([
        this.apiRequest("/lead/events"),
        this.apiRequest("/lead/coordinators"),
      ]);
      if (eventsResponse.ok) this.leadEvents = eventsResponse.data.map(event => ({ ...event, selectedCoordinatorId: event.assigned_coordinator_id || "" }));
      if (coordinatorsResponse.ok) this.coordinators = coordinatorsResponse.data;
      this.loading = false;
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
    },

    isVenueStaff() { return this.normalizeRole(this.user?.role) === "Venue Staff"; },

    async fetchVenueWorkspace() {
      if (!this.isVenueStaff()) return;
      if (!this.venueWorkspace) this.venueWorkspace = {
        venues: [], bookings: [], busy: false, loaded: false, bookingError: false,
        bookingFilter: "All", showCreate: false, saving: false, form: emptyVenueForm(),
        // [SCRUM-92] the venue being edited, the form's message, a pending retire, and the last save's result
        editing: null, formMessage: null, retire: null, outcome: null,
      };
      const workspace = this.venueWorkspace;
      if (workspace.busy) return;
      workspace.busy = true;
      const recipient = this.user;
      try {
        const venues = await this.apiRequest("/venues");
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        if (!venues.ok || !Array.isArray(venues.data)) { workspace.loaded = false; return; }
        workspace.venues = venues.data;
        workspace.loaded = true;
        const results = await Promise.all(venues.data.map(async venue => {
          const response = await this.apiRequest(`/venues/${venue.id}/bookings`);
          return { venue, response };
        }));
        if (this.user !== recipient || this.venueWorkspace !== workspace) return;
        workspace.bookingError = results.some(({ response }) => !response.ok || !Array.isArray(response.data));
        workspace.bookings = results.flatMap(({ venue, response }) => response.ok && Array.isArray(response.data)
          ? response.data.map(booking => ({ ...booking, venue_name: venue.name })) : []);
      } finally {
        if (this.venueWorkspace === workspace) workspace.busy = false;
      }
    },

    async createVenue() {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || workspace.saving || workspace.busy) return;
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

    showVenueFormErrors(response) {
      const { fields, message } = formErrorsFrom(response);
      this.fieldErrors = fields;
      this.venueWorkspace.formMessage = message ? { kind: "error", text: message } : null;
      this.clearAlert();
    },

    // The buttons wait for a save in flight: closing the form mid-save would
    // drop its result. closeVenueForm() itself has no guard, because a good
    // save calls it while saving is still true.
    toggleVenueCreate() {
      const workspace = this.venueWorkspace;
      if (!this.isVenueStaff() || !workspace || workspace.saving) return;
      const opening = !workspace.showCreate || !!workspace.editing;
      this.closeVenueForm();
      workspace.showCreate = opening;
    },

    cancelVenueForm() {
      if (!this.venueWorkspace?.saving) this.closeVenueForm();
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
      if (!this.isVenueStaff() || !workspace || workspace.saving) return;
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
      if (!this.isVenueStaff() || !venue || workspace.saving || workspace.busy) return;
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
      if (!this.isVenueStaff() || !workspace) return;
      workspace.retire = { venue, busy: false, error: "" };
    },

    cancelRetireVenue() {
      const workspace = this.venueWorkspace;
      if (workspace && !workspace.retire?.busy) workspace.retire = null;
    },

    async confirmRetireVenue() {
      const workspace = this.venueWorkspace;
      const retire = workspace?.retire;
      if (!this.isVenueStaff() || !retire || retire.busy) return;
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

    async openStaffBooking(bookingId) {
      if (!this.isVenueStaff()) return;
      this.clearAlert(); this.fieldErrors = {};
      this.staffBooking = { id: bookingId, detail: null, reason: "", busy: true };
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
      review.busy = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/bookings/${review.id}/${action}`, {
          method: "POST", ...(action === "reject" ? { body: { reason: review.reason.trim() } } : {}),
        });
        if (this.user !== recipient || this.staffBooking !== review) return;
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

    async markNotificationRead(notification) {
      const recipient = this.user;
      const response = await this.apiRequest(`/notifications/${notification.id}/read`, { method: "POST" });
      if (this.user === recipient && response.ok) this.notifications = this.notifications.map(item => item.id === notification.id ? response.data : item);
    },

    async openNotification(notification) {
      if (!notification.read) await this.markNotificationRead(notification);
      if (notification.booking_id && this.isVenueStaff()) {
        this.notificationOpen = false;
        await this.openStaffBooking(notification.booking_id);
      }
    },

    canBookEvent(event) {
      const role = this.normalizeRole(this.user?.role);
      const events = role === "Event Coordinator Lead" ? this.leadEvents : role === "Coordinator" ? this.assignedEvents : [];
      return !!event && events.some(item => item.id === event.id);
    },

    async openBookings(event) {
      if (!this.canBookEvent(event)) return;
      this.clearAlert();
      this.fieldErrors = {};
      this.bookingDialog = {
        event, venues: [], bookings: [], busy: true, submitting: false, ready: false, bookingsLoaded: false,
        form: { venue_id: "", booking_start: "", booking_end: "" },
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
        if (dialog.bookingsLoaded) dialog.bookings = bookings.data;
        else if (bookings.ok) this.showAlert("Booking history returned an invalid response. Refresh bookings to try again.");
      } finally {
        if (this.bookingDialog === dialog) dialog.busy = false;
      }
    },

    closeBookings() { this.bookingDialog = null; this.fieldErrors = {}; },

    async submitBooking() {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.busy || !dialog.ready || !this.canBookEvent(dialog.event)) return;
      this.fieldErrors = {};
      this.clearAlert();
      const { venue_id, booking_start, booking_end } = dialog.form;
      if (!dialog.venues.some(venue => venue.id === Number(venue_id))) this.fieldErrors.venue_id = "Select a venue";
      if (!booking_start || !Number.isFinite(Date.parse(booking_start))) this.fieldErrors.booking_start = "Enter a valid start date and time";
      else if (Date.parse(booking_start) <= Date.now()) this.fieldErrors.booking_start = "Start must be in the future";
      if (!booking_end || !Number.isFinite(Date.parse(booking_end)) || Date.parse(booking_end) <= Date.parse(booking_start)) this.fieldErrors.booking_end = "End must be after start";
      if (Object.keys(this.fieldErrors).length) return;
      dialog.busy = true;
      dialog.submitting = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/venues/${Number(venue_id)}/bookings`, {
          method: "POST", body: { event_id: dialog.event.id, booking_start, booking_end },
        });
        if (this.user !== recipient || this.bookingDialog !== dialog || !response.ok) return;
        dialog.form = { venue_id: "", booking_start: "", booking_end: "" };
        this.showAlert("Venue booking request submitted.", "success");
        const bookings = await this.apiRequest(`/events/${dialog.event.id}/bookings`);
        if (this.user === recipient && this.bookingDialog === dialog) {
          dialog.bookingsLoaded = bookings.ok && Array.isArray(bookings.data);
          if (dialog.bookingsLoaded) dialog.bookings = bookings.data;
        }
      } finally {
        if (this.bookingDialog === dialog) {
          dialog.busy = false;
          dialog.submitting = false;
        }
      }
    },

    async saveAssignment(event) {
      if (!event.selectedCoordinatorId) { this.showAlert("Select a Coordinator first."); return; }
      const action = event.assigned_coordinator_id ? "reassign" : "assign";
      const response = await this.apiRequest(`/events/${event.id}/${action}`, { method: "POST", body: { coordinator_id: Number(event.selectedCoordinatorId) } });
      if (response.ok) {
        await this.fetchLeadData();
        this.showAlert(`Event request #${event.id} ${action === "assign" ? "assigned" : "reassigned"}.`, "success");
      }
    },

    async startReview(eventId) { const response = await this.apiRequest(`/events/${eventId}/review`, { method: "POST" }); if (response.ok) { await this.fetchAssignedEvents(); this.showAlert("Review started.", "success"); } },
    async handleApprove(eventId) { const response = await this.apiRequest(`/events/${eventId}/approve`, { method: "POST" }); if (response.ok) { await this.fetchAssignedEvents(); this.showAlert("Event request approved.", "success"); } },
    async handleReject(eventId, reason) { const response = await this.apiRequest(`/events/${eventId}/reject`, { method: "POST", body: { reason } }); if (response.ok) { this.closeModal(); await this.fetchAssignedEvents(); this.showAlert("Event request rejected.", "success"); } },
    async handleReturn(eventId, reason) { const response = await this.apiRequest(`/events/${eventId}/return`, { method: "POST", body: { reason } }); if (response.ok) { this.closeModal(); await this.fetchAssignedEvents(); this.showAlert("Clarification requested from the organiser.", "success"); } },

    openDecision(event, kind) { this.fieldErrors = {}; this.decisionForm = { reason: "" }; this.modal = { open: true, kind, event }; },
    openAmend(event) { this.fieldErrors = {}; this.amendForm = { title: event.title || "", description: event.description || "" }; this.modal = { open: true, kind: "amend", event }; },
    closeModal() { this.modal = { open: false, kind: "", event: null }; this.fieldErrors = {}; },
    async submitModal() { const eventId = this.modal.event.id; if (this.modal.kind === "amend") return this.amendEvent(eventId, this.amendForm); if (!this.decisionForm.reason.trim()) { this.fieldErrors = { reason: "A reason is required" }; return; } return this.modal.kind === "reject" ? this.handleReject(eventId, this.decisionForm.reason) : this.handleReturn(eventId, this.decisionForm.reason); },
    statusClass(status) { return { Submitted: "status-waiting", "Under Review": "status-review", Approved: "status-approved", Rejected: "status-rejected", Returned: "status-returned" }[status] || "status-neutral"; },
    formatDate(value) { return value ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not set"; },
  }));
});


