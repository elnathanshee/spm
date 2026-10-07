const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

document.addEventListener("alpine:init", () => {
  Alpine.data("appState", () => ({
    user: null,
    currentPath: "/login",
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
      { path: "/events", label: "My events", role: "Organiser" },
      { path: "/events/new", label: "New request", role: "Organiser" },
      { path: "/coordinator/events", label: "Assigned requests", role: "Coordinator" },
      { path: "/venue", label: "Venue operations", role: "Venue Staff" },
      { path: "/technical", label: "Technical support", role: "Technical Support" },
      { path: "/attendee", label: "Event directory", role: "Attendee" },
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
      if (response.ok) { this.showAlert("Draft saved.", "success"); this.navigate("/events"); }
    },

    async submitEvent(eventId) {
      const response = await this.apiRequest(`/events/${eventId}/submit`, { method: "POST" });
      if (response.ok) await this.fetchEvents();
    },

    async amendEvent(eventId, data) {
      this.fieldErrors = {};
      const response = await this.apiRequest(`/events/${eventId}/amend`, { method: "POST", body: data });
      if (response.ok) { this.closeModal(); await this.fetchEvents(); }
    },

    async fetchAssignedEvents() {
      this.loading = true;
      const response = await this.apiRequest("/coordinator/events/data");
      if (response.ok) this.assignedEvents = response.data;
      this.loading = false;
    },

    async startReview(eventId) { const response = await this.apiRequest(`/events/${eventId}/review`, { method: "POST" }); if (response.ok) await this.fetchAssignedEvents(); },
    async handleApprove(eventId) { const response = await this.apiRequest(`/events/${eventId}/approve`, { method: "POST" }); if (response.ok) await this.fetchAssignedEvents(); },
    async handleReject(eventId, reason) { const response = await this.apiRequest(`/events/${eventId}/reject`, { method: "POST", body: { reason } }); if (response.ok) { this.closeModal(); await this.fetchAssignedEvents(); } },
    async handleReturn(eventId, reason) { const response = await this.apiRequest(`/events/${eventId}/return`, { method: "POST", body: { reason } }); if (response.ok) { this.closeModal(); await this.fetchAssignedEvents(); } },

    openDecision(event, kind) { this.fieldErrors = {}; this.decisionForm = { reason: "" }; this.modal = { open: true, kind, event }; },
    openAmend(event) { this.fieldErrors = {}; this.amendForm = { title: event.title || "", description: event.description || "" }; this.modal = { open: true, kind: "amend", event }; },
    closeModal() { this.modal = { open: false, kind: "", event: null }; this.fieldErrors = {}; },
    async submitModal() { const eventId = this.modal.event.id; if (this.modal.kind === "amend") return this.amendEvent(eventId, this.amendForm); if (!this.decisionForm.reason.trim()) { this.fieldErrors = { reason: "A reason is required" }; return; } return this.modal.kind === "reject" ? this.handleReject(eventId, this.decisionForm.reason) : this.handleReturn(eventId, this.decisionForm.reason); },
    statusClass(status) { return { Submitted: "status-waiting", "Under Review": "status-review", Approved: "status-approved", Rejected: "status-rejected", Returned: "status-returned" }[status] || "status-neutral"; },
    formatDate(value) { return value ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not set"; },
  }));
});


