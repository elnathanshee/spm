const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

document.addEventListener("alpine:init", () => {
  Alpine.data("appState", () => ({
    user: null,
    currentPath: "/login",
    sidebarOpen: false,
    loading: false,
    accessDenied: false,
    unbuiltRole: false,
    events: [],
    assignedEvents: [],
    alert: { message: "", kind: "error" },
    fieldErrors: {},
    loginForm: { username: "", password: "" },
    draft: { title: "", purpose: "", description: "", proposed_start: "", expected_attendance: null, venue_requirements: "" },
    amendForm: { title: "", description: "" },
    decisionForm: { reason: "" },
    modal: { open: false, kind: "", event: null },
    navigation: [
      { path: "/events", label: "My event requests", role: "Organiser", icon: "calendar" },
      { path: "/events/new", label: "Create request", role: "Organiser", icon: "plus" },
      { path: "/coordinator/events", label: "Assigned requests", role: "Coordinator", icon: "inbox" },
      { path: "/venue", label: "Venue operations", role: "Venue Staff", icon: "building" },
      { path: "/technical", label: "Technical support", role: "Technical Support", icon: "equipment" },
      { path: "/attendee", label: "Event directory", role: "Attendee", icon: "ticket" },
    ],

    async init() {
      await this.loadSession();
      window.addEventListener("hashchange", () => {
        if (this.user) this.navigate(this.requestedPath(), false);
      });
    },

    normalizeRole(role) {
      const roles = { organiser: "Organiser", coordinator: "Coordinator", "venue staff": "Venue Staff", "technical support": "Technical Support", attendee: "Attendee" };
      return roles[String(role || "").trim().toLowerCase()] || role;
    },

    async apiRequest(path, options = {}) {
      const request = { ...options, credentials: "include", headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } };
      if (request.body && typeof request.body !== "string") request.body = JSON.stringify(request.body);
      const response = await fetch(`${API_BASE}${path}`, request);
      const data = await response.json().catch(() => ({}));
      if (response.ok) return { ok: true, status: response.status, data };
      this.handleHttpError(response.status, data, path);
      return { ok: false, status: response.status, data };
    },

    handleHttpError(status, data, path) {
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
        this.showAlert(`403 Forbidden: Role '${this.user?.role || "unknown"}' is not authorized to perform this function.`);
        return;
      }
      if (status === 422) {
        this.fieldErrors = this.parseValidationErrors(data.detail);
        return;
      }
      this.showAlert(data.detail || `Request failed with status ${status}.`);
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
      this.navigate(this.requestedPath() || this.defaultPath());
    },

    async login(username, password) {
      this.loading = true;
      this.clearAlert();
      const response = await this.apiRequest("/auth/login", { method: "POST", body: { username, password } });
      this.loading = false;
      if (!response.ok) return;
      this.user = { user_id: response.data.user_id, username: response.data.username, role: this.normalizeRole(response.data.role) };
      this.loginForm.password = "";
      this.navigate(this.defaultPath());
    },

    async logout() {
      await this.apiRequest("/auth/logout", { method: "POST" });
      this.user = null;
      this.currentPath = "/login";
      this.events = [];
      this.assignedEvents = [];
    },

    defaultPath() { return this.user?.role === "Coordinator" ? "/coordinator/events" : this.user?.role === "Organiser" ? "/events" : this.rolePath(this.user?.role); },
    rolePath(role) { return { "Venue Staff": "/venue", "Technical Support": "/technical", Attendee: "/attendee" }[role] || "/events"; },
    requestedPath() { return window.location.hash.startsWith("#/") ? window.location.hash.slice(1) : ""; },
    canAccess(targetRole, userRole) { return targetRole === userRole; },
    pageTitle() { if (this.accessDenied) return "Access denied"; return this.navigation.find(entry => entry.path === this.currentPath)?.label || "Workspace"; },
    roleWorkspace() { return { Organiser: "Event requests", Coordinator: "Planning & review", "Venue Staff": "Venue operations", "Technical Support": "Technical operations", Attendee: "My events" }[this.user?.role] || "Event services"; },
    rolePageHeading() { return { "Venue Staff": "Venue operations", "Technical Support": "Technical support", Attendee: "Event directory" }[this.user?.role] || "Your workspace"; },
    rolePageDescription() { return { "Venue Staff": "Review venue-related work assigned to your role.", "Technical Support": "Keep track of technical and equipment arrangements assigned to you.", Attendee: "Find attendee-facing event information available to your role." }[this.user?.role] || "Your role-specific view of ConnectSphere."; },
    roleEmptyMessage() { return { "Venue Staff": "There are no venue requests available right now. Relevant venue work will appear here when supported by the event workflow.", "Technical Support": "There are no technical requirements available right now. Assigned equipment and support work will appear here.", Attendee: "There are no attendee events available right now. Events open to your role will appear here." }[this.user?.role] || "There is no work requiring your attention right now."; },
    roleIcon() { return { "Venue Staff": "building", "Technical Support": "equipment", Attendee: "ticket" }[this.user?.role] || "calendar"; },
    userInitials() { return String(this.user?.username || "U").split(/[._\s-]+/).map(part => part[0]).join("").slice(0, 2).toUpperCase(); },
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
      };
      return icons[name] || icons.calendar;
    },

    navigate(path, updateLocation = true) {
      const item = this.navigation.find(entry => entry.path === path);
      this.accessDenied = !item || !this.canAccess(item.role, this.user?.role);
      this.unbuiltRole = ["/venue", "/technical", "/attendee"].includes(path) && !this.accessDenied;
      this.currentPath = this.accessDenied ? "/access-denied" : path;
      if (updateLocation && window.location.hash !== `#${path}`) window.location.hash = path;
      this.clearAlert();
      if (!this.accessDenied && path === "/events") this.fetchEvents();
      if (!this.accessDenied && path === "/coordinator/events") this.fetchAssignedEvents();
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


