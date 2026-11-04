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
    venueCalendar: null, // [SCRUM-96] the venue availability calendar dialog
    equipmentWorkspace: null, // [SCRUM-93] the Technical Support equipment worklist
    staffBooking: null,
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
      await this.loadSession();
      window.setInterval(() => this.refreshWorkspace(), 15000);
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
      if (Array.isArray(detail) && detail.length) return detail.map(error => error.msg).filter(Boolean).join(" ") || "The request was not valid.";
      if (response?.status === 403) return "You don't have permission to perform this action.";
      return `The request failed${response?.status ? ` (status ${response.status})` : ""}. Please try again.`;
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
    formatNotificationTime(value) {
      if (!value) return "Recently";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "Recently";
      return new Intl.DateTimeFormat("en-SG", { dateStyle: "medium", timeStyle: "short" }).format(date);
    },
    formatNotificationMessage(notification) { return this.formatServerTimes(notification?.message); },
    // [SCRUM-95] A time marked "UTC", Z or an offset is converted to local time. Unmarked times are
    // the backend's Singapore time and are shown as written. "D 10:00 - 12:00" ends that day or the next.
    formatServerTimes(text) {
      return String(text || "").replace(
        /(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?(?: - (\d{2}:\d{2}))?( UTC)?/g,
        (match, day, time, zone, endTime, utc) => {
          const suffix = zone || (utc ? "Z" : "");
          const start = new Date(`${day}T${time}${suffix}`);
          if (Number.isNaN(start.getTime())) return match;
          if (!endTime) return this.formatDate(start);
          let end = new Date(`${day}T${endTime}${suffix}`);
          if (end <= start) end = new Date(end.getTime() + 24 * 60 * 60 * 1000);
          return `${this.formatDate(start)} – ${start.toDateString() === end.toDateString() ? this.formatTime(end) : this.formatDate(end)}`;
        },
      );
    },
    bookingStatusClass(status) { return { Pending: "status-waiting", Confirmed: "status-approved", Rejected: "status-rejected", Expired: "status-rejected" }[status] || "status-neutral"; },
    utcTimestamp(value) { return value && !/(Z|[+-]\d{2}:?\d{2})$/i.test(value) ? `${value}Z` : value; },
    parseUtc(value) { return value ? new Date(this.utcTimestamp(value)) : null; },
    formatHoldExpiry(value) {
      const timestamp = this.utcTimestamp(value);
      return timestamp && Number.isFinite(Date.parse(timestamp)) ? this.formatDate(timestamp) : "Not set";
    },
    // [SCRUM-95] Booking times are stored in UTC (D7), like hold times.
    formatSchedule(start, end) { return `${this.formatHoldExpiry(start)} – ${this.formatHoldExpiry(end)}`; },
    // [SCRUM-89] AC5: the requester by name.
    requesterLabel(detail) { return detail?.requester_name || "Not recorded"; },
    formatUtc(value) { return value ? this.formatDate(this.parseUtc(value).toISOString()) : "Not set"; },
    listText(items) { return Array.isArray(items) && items.length ? items.join(", ") : "Not recorded"; },
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
      if (!this.accessDenied && path === "/technical") this.fetchEquipmentWorkspace(); // [SCRUM-93]
    },

    async fetchEvents() {
      this.loading = true;
      const response = await this.apiRequest("/events");
      if (response.ok) this.events = response.data;
      this.loading = false;
    },

    async createEventDraft(data) {
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
      const response = await this.apiRequest("/events", { method: "POST", body: payload });
      if (response.ok) {
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
        this.navigate("/events");
        this.showAlert("Your event request was saved as a draft.", "success");
      }
    },

    async submitEvent(eventId) {
      const response = await this.apiRequest(`/events/${eventId}/submit`, { method: "POST" });
      if (response.ok) { await this.fetchEvents(); this.showAlert("Event request submitted for review.", "success"); }
    },

    async amendEvent(eventId, data) {
      this.fieldErrors = {};
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

      if (!Object.keys(payload).length) {
        this.showAlert("No changes to save.", "info");
        return;
      }

      const response = await this.apiRequest(`/events/${eventId}/amend`, { method: "POST", body: payload });
      if (response.ok) { this.closeModal(); await this.fetchEvents(); this.showAlert("Event request updated.", "success"); }
    },

    async fetchAssignedEvents() {
      const recipient = this.user;
      this.loading = true;
      const response = await this.apiRequest("/coordinator/events/data");
      if (this.user === recipient && response.ok) this.assignedEvents = response.data;
      if (this.user === recipient) this.loading = false;
    },
    async fetchAllCoordinatorEvents() {
      const recipient = this.user;
      const response = await this.apiRequest("/coordinator/events/all-data");
      if (this.user === recipient && response.ok) this.allCoordinatorEvents = response.data;
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
      if (this.currentPath === "/technical") await this.fetchEquipmentWorkspace(); // [SCRUM-93]
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

    async openStaffBooking(bookingId) {
      if (!this.isVenueStaff()) return;
      this.clearAlert(); this.fieldErrors = {};
      this.staffBooking = { id: bookingId, detail: null, reason: "", busy: true, holdExpiry: "", error: "" };
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
      const expiry = review.holdExpiry ? new Date(review.holdExpiry) : null;
      if (!expiry || Number.isNaN(expiry.getTime())) { this.fieldErrors.holdExpiry = "Choose the new expiry date and time"; return; }
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

    async openNotification(notification) {
      if (!notification.read) await this.markNotificationRead(notification);
      if (notification.booking_id && this.isVenueStaff()) {
        this.notificationOpen = false;
        await this.openStaffBooking(notification.booking_id);
      }
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
      return this.isOwnPendingHold(booking) && Date.parse(this.utcTimestamp(booking.hold_expires_at)) > Date.now();
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
        checkingSuitability: false,
        suitabilityResults: [],
        suitabilityError: "",
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

    async searchVenues() {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.searching) return;
      dialog.searchError = "";
      dialog.searchMessage = "";
      dialog.searching = true;
      const recipient = this.user;
      try {
        const search = dialog.search || {};
        const queryParts = [];
        // [SCRUM-95] The inputs hold local times; send them in UTC (D7), as the
        // booking submit does. An unreadable value goes as typed for the API to reject.
        const utc = value => Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : value;
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
        if (response.ok) {
          dialog.searchResults = Array.isArray(response.data) ? response.data : [];
          if (!dialog.searchResults.length) {
            dialog.searchMessage = "No venues found matching your criteria.";
          }
        } else {
          dialog.searchError = this.dialogErrorMessage(response);
          dialog.searchResults = [];
        }
      } finally {
        if (this.bookingDialog === dialog) dialog.searching = false;
      }
    },

    async checkSuitability(facilities) {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.checkingSuitability) return;
      dialog.suitabilityError = "";
      dialog.suitabilityResults = [];
      dialog.checkingSuitability = true;
      const recipient = this.user;
      try {
        const query = facilities ? `?facilities=${encodeURIComponent(facilities)}` : "";
        const response = await this.apiRequest(`/events/${dialog.event.id}/venue-suitability${query}`, { inlineErrors: true });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        if (response.ok) {
          dialog.suitabilityResults = Array.isArray(response.data?.venues)
            ? response.data.venues
            : Array.isArray(response.data)
              ? response.data
              : [];
        } else {
          dialog.suitabilityError = this.dialogErrorMessage(response);
        }
      } finally {
        if (this.bookingDialog === dialog) dialog.checkingSuitability = false;
      }
    },

    selectVenueFromSearch(venue) {
      const dialog = this.bookingDialog;
      if (!dialog || !venue) return;
      dialog.form.venue_id = String(venue.id);
      if (dialog.search?.start_time) dialog.form.booking_start = dialog.search.start_time;
      if (dialog.search?.end_time) dialog.form.booking_end = dialog.search.end_time;
      if (Array.isArray(dialog.venues) && !dialog.venues.some(v => v.id === venue.id)) {
        dialog.venues.push(venue);
      }
    },

    // ---------------------------------------------------------------------
    // [SCRUM-93] Equipment requirements (SCRUM-39 endpoints)
    // ---------------------------------------------------------------------

    isTechnicalSupport() { return this.normalizeRole(this.user?.role) === "Technical Support"; },

    canViewEventEquipment() {
      // AC2 names the Coordinator and the Lead for the event view.
      return ["Coordinator", "Event Coordinator Lead"].includes(this.normalizeRole(this.user?.role));
    },

    canRecordEquipment(event) {
      // AC1: the assigned Coordinator, or a Lead for any event. Mirrors the
      // backend's own rule (Week 7 Change #5).
      const role = this.normalizeRole(this.user?.role);
      if (role === "Event Coordinator Lead") return true;
      return role === "Coordinator" && !!event && event.assigned_coordinator_id === this.user?.user_id;
    },

    emptyEquipmentForm() { return { equipment_type: "", quantity: "", technical_requirements: "" }; },

    async fetchEventEquipment(eventId) {
      const dialog = this.eventDetail;
      if (!dialog || !this.canViewEventEquipment()) return;
      if (!dialog.equipment) dialog.equipment = { items: [], loading: false, loaded: false, error: "", formError: "", saving: false, form: this.emptyEquipmentForm() };
      const equipment = dialog.equipment;
      equipment.loading = true;
      equipment.error = "";
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${eventId}/equipment-requests`, { inlineErrors: true });
        if (this.user !== recipient || this.eventDetail !== dialog) return;
        if (!response.ok) {
          equipment.loaded = false;
          equipment.error = this.dialogErrorMessage(response);
          return;
        }
        equipment.items = Array.isArray(response.data?.equipment_requests) ? response.data.equipment_requests : [];
        equipment.loaded = true;
      } finally {
        if (this.eventDetail === dialog) equipment.loading = false;
      }
    },

    equipmentFormErrors(form) {
      // D1: immediate feedback for the two rules the Coordinator can see.
      const errors = {};
      if (!String(form?.equipment_type ?? "").trim()) errors.equipment_type = "Enter the equipment type";
      const quantity = Number(form?.quantity);
      if (form?.quantity === "" || form?.quantity === null || form?.quantity === undefined || !Number.isFinite(quantity) || quantity <= 0) {
        errors.quantity = "Enter a quantity above zero";
      } else if (!Number.isInteger(quantity)) {
        errors.quantity = "Enter a whole number";
      }
      return errors;
    },

    async recordEquipment() {
      const dialog = this.eventDetail;
      const equipment = dialog?.equipment;
      if (!dialog || !equipment || equipment.saving) return;
      if (!this.canRecordEquipment(dialog.detail)) return;

      this.fieldErrors = this.equipmentFormErrors(equipment.form);
      equipment.formError = "";
      if (Object.keys(this.fieldErrors).length) return;

      const body = {
        equipment_type: String(equipment.form.equipment_type).trim(),
        quantity: Number(equipment.form.quantity),
      };
      const notes = String(equipment.form.technical_requirements ?? "").trim();
      if (notes) body.technical_requirements = notes;

      equipment.saving = true;
      const recipient = this.user;
      try {
        const response = await this.apiRequest(`/events/${dialog.id}/equipment-requests`, { method: "POST", body, inlineErrors: true });
        if (this.user !== recipient || this.eventDetail !== dialog) return;
        if (!response.ok) {
          // D1: the server's own field errors land on the same fields.
          const fields = this.parseValidationErrors(response.data?.detail);
          if (Object.keys(fields).length) this.fieldErrors = fields;
          // D2: a duplicate is about the whole line, so it sits above the form.
          else equipment.formError = this.dialogErrorMessage(response);
          return;
        }
        equipment.form = this.emptyEquipmentForm();
        this.fieldErrors = {};
        await this.fetchEventEquipment(dialog.id);
      } finally {
        if (this.eventDetail === dialog) equipment.saving = false;
      }
    },

    async fetchEquipmentWorkspace() {
      // AC3, AC4: the Technical Support worklist only.
      if (!this.isTechnicalSupport()) return;
      if (!this.equipmentWorkspace) this.equipmentWorkspace = { items: [], busy: false, loaded: false, error: "" };
      const workspace = this.equipmentWorkspace;
      if (workspace.busy) return;
      workspace.busy = true;
      workspace.error = "";
      const recipient = this.user;
      try {
        // D3: the backend decides what this role may see; nothing is filtered here.
        const response = await this.apiRequest("/equipment-requests", { inlineErrors: true });
        if (this.user !== recipient || this.equipmentWorkspace !== workspace) return;
        if (!response.ok) {
          workspace.loaded = false;
          workspace.error = this.dialogErrorMessage(response);
          return;
        }
        workspace.items = Array.isArray(response.data?.equipment_requests) ? response.data.equipment_requests : [];
        workspace.loaded = true;
      } finally {
        if (this.equipmentWorkspace === workspace) workspace.busy = false;
      }
    },

    equipmentNotes(item) { return item?.technical_requirements || "No special requirements"; },

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

    calendarDayIsFree(day) { return !(Array.isArray(day?.busy) && day.busy.length); },

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

    async submitBooking() {
      const dialog = this.bookingDialog;
      if (!dialog || dialog.busy || !dialog.ready || !this.canBookEvent(dialog.event)) return;
      this.fieldErrors = {};
      this.clearAlert();
      dialog.error = "";
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
          // [SCRUM-95] The inputs hold local times; send them in UTC (D7).
          method: "POST", inlineErrors: true, body: { event_id: dialog.event.id, booking_start: new Date(booking_start).toISOString(), booking_end: new Date(booking_end).toISOString() },
        });
        if (this.user !== recipient || this.bookingDialog !== dialog) return;
        if (!response.ok) {
          dialog.error = this.dialogErrorMessage(response);
          return;
        }
        dialog.form = { venue_id: "", booking_start: "", booking_end: "" };
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

    async saveAssignment(event) {
      if (!event.selectedCoordinatorId) { this.showAlert("Select a Coordinator first."); return; }
      const action = event.assigned_coordinator_id ? "reassign" : "assign";
      const response = await this.apiRequest(`/events/${event.id}/${action}`, { method: "POST", body: { coordinator_id: Number(event.selectedCoordinatorId) } });
      if (response.ok) {
        await this.fetchLeadData();
        this.showAlert(`Event request #${event.id} ${action === "assign" ? "assigned" : "reassigned"}.`, "success");
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
        if (response.ok) {
          dialog.detail = response.data;
          // [SCRUM-93] AC2: the equipment panel belongs to the Coordinator and
          // the Lead. D4: an Organiser opens this same view and must not make a
          // call their role is not entitled to.
          if (this.canViewEventEquipment()) await this.fetchEventEquipment(eventId);
        }
        else dialog.error = response.status === 403 ? "You don't have permission to view this request." : response.status === 404 ? "Event request not found." : "Request details could not be loaded. Please try again.";
      } finally { if (this.user === recipient && this.eventDetail === dialog) dialog.loading = false; }
    },
    eventFieldLabel(key) { return ({ organiser: "Organiser", assigned_coordinator: "Assigned Coordinator", proposed_start: "Proposed date and time", clarification_note: "Latest clarification note" })[key] || key.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase()); },
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
    openAmend(event) {
      this.fieldErrors = {};
      this.clearAlert();
      this.amendOriginal = {
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
      this.amendForm = { ...this.amendOriginal };
      this.modal = { open: true, kind: "amend", event };
    },
    closeModal() { this.modal = { open: false, kind: "", event: null }; this.fieldErrors = {}; this.amendOriginal = null; },
    async submitModal() { if (this.decisionBusy || !this.modal.open) return; const eventId = this.modal.event.id; if (this.modal.kind === "amend") return this.amendEvent(eventId, this.amendForm); if (!this.decisionForm.reason.trim()) { this.fieldErrors = { reason: "A reason is required" }; return; } return this.modal.kind === "reject" ? this.handleReject(eventId, this.decisionForm.reason) : this.handleReturn(eventId, this.decisionForm.reason); },
    statusClass(status) { return { Submitted: "status-waiting", "Under Review": "status-review", Approved: "status-approved", Rejected: "status-rejected", Returned: "status-returned" }[status] || "status-neutral"; },
    suitabilityBadgeClass(status) { return { suitable: "status-approved", unsuitable: "status-rejected", unknown: "status-waiting" }[status] || "status-neutral"; },
    availabilityClass(available) { return available ? "status-approved" : "status-rejected"; },
    formatDate(value) { return value ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not set"; },
    formatTime(value) { return new Intl.DateTimeFormat("en", { timeStyle: "short" }).format(new Date(value)); },
  }));
});
