// [SCRUM-86] The Safety Officer's workspace: events waiting for a check, what each one
// needs checking, and the decision. Uses the parent scope: apiRequest, formatDate, formatSchedule.
const DECISION_LABELS = { approve: "Approved", reject: "Rejected", request_changes: "Changes requested" };

// A 422 from the decision route lists its errors; other refusals are one sentence.
function refusalMessage(response) {
  const detail = response.data?.detail;
  if (Array.isArray(detail)) return detail.map(error => String(error.msg || "").replace(/^Value error, /, "")).join(" ");
  return detail || "The decision could not be saved. Please try again.";
}

export function safetyWorkspace() {
  return {
    safety: {
      items: [], loaded: false, loading: false, error: "", notice: "",
      selected: null, detailLoading: false, detailError: "",
      reason: "", reasonError: "", formError: "", busy: false,
    },

    async init() {
      await this.loadSafetyEvents();
    },

    async loadSafetyEvents() {
      const view = this.safety;
      view.loading = true;
      const response = await this.apiRequest("/safety/events", { inlineErrors: true });
      view.loading = false;
      if (response.ok) {
        view.items = response.data;
        view.loaded = true;
        return;
      }
      view.error = response.data?.detail || "The events waiting for a safety check could not be loaded.";
    },

    // The list changes as Coordinators submit and cancel, and this page has no poll.
    async refreshSafetyEvents() {
      this.safety.error = "";
      await this.loadSafetyEvents();
    },

    async openSafetyEvent(eventId) {
      const view = this.safety;
      view.detailLoading = true;
      view.detailError = "";
      view.notice = "";
      this.resetSafetyForm();
      const response = await this.apiRequest(`/safety/events/${eventId}`, { inlineErrors: true });
      view.detailLoading = false;
      if (response.ok) view.selected = response.data;
      else {
        view.selected = null;
        view.detailError = response.data?.detail || "This event's details could not be loaded.";
      }
    },

    closeSafetyEvent() {
      if (this.safety.busy) return;
      this.safety.selected = null;
      this.resetSafetyForm();
    },

    resetSafetyForm() {
      this.safety.reason = "";
      this.safety.reasonError = "";
      this.safety.formError = "";
    },

    overCapacity(event, venue) {
      return event.expected_attendance != null && venue.capacity != null && event.expected_attendance > venue.capacity;
    },

    listOrNone(items) {
      return items && items.length ? items.join(", ") : "Not recorded";
    },

    decisionLabel(decision) {
      return DECISION_LABELS[decision] || decision;
    },

    decisionNotice(decision, title) {
      if (decision === "approve") return `"${title}" is now Confirmed.`;
      const outcome = decision === "reject" ? "The safety arrangement was rejected" : "Changes were requested";
      return `${outcome} for "${title}". It is back in Planning and the Coordinator has been told.`;
    },

    async submitSafetyDecision(decision) {
      const view = this.safety;
      if (view.busy || !view.selected) return;
      const reason = String(view.reason ?? "").trim();
      if (decision !== "approve" && !reason) {
        view.reasonError = "Enter a reason when you reject or request changes.";
        return;
      }
      view.reasonError = "";
      view.formError = "";
      view.busy = true;
      const { id, title } = view.selected;
      try {
        const response = await this.apiRequest(`/safety/events/${id}/decision`, {
          method: "POST", body: { decision, reason: reason || null }, inlineErrors: true,
        });
        if (response.ok) {
          view.selected = null;
          view.notice = this.decisionNotice(decision, title);
          this.resetSafetyForm();
          await this.loadSafetyEvents();
        } else if (response.status === 404) {
          // Someone else decided it, or it was cancelled: the list is out of date.
          view.selected = null;
          view.error = refusalMessage(response);
          await this.loadSafetyEvents();
        } else {
          view.formError = refusalMessage(response);
        }
      } finally {
        view.busy = false;
      }
    },
  };
}
