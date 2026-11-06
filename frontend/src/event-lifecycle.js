// [SCRUM-102] The lifecycle actions in the event detail dialog. GET /events/{id}/lifecycle-actions
// says what the signed-in person may do and why a button is unavailable, so the rules stay in
// status_rules.py and aren't repeated here. Uses the parent scope: apiRequest, eventDetail, user.
export function eventLifecycle() {
  return {
    lifecycle: { loaded: false, actions: [], busy: false, error: "", notice: "", cancelling: false, reason: "", reasonError: "" },

    async init() {
      await this.loadLifecycleActions();
    },

    async loadLifecycleActions() {
      const view = this.lifecycle;
      // The dialog may have been closed while a request was running.
      if (!this.eventDetail) return;
      const response = await this.apiRequest(`/events/${this.eventDetail.id}/lifecycle-actions`, { inlineErrors: true });
      if (response.ok) {
        view.actions = response.data;
        view.loaded = true;
      } else {
        view.error = response.data?.detail || "The actions for this event could not be loaded.";
      }
    },

    lifecycleUnavailableReasons() {
      return this.lifecycle.actions.filter(item => !item.available);
    },

    async runLifecycleAction(item) {
      const view = this.lifecycle;
      if (!item.available || view.busy) return;
      view.notice = "";
      view.error = "";
      // Cancelling needs a reason first, so its button opens the form.
      if (item.action === "cancel") {
        view.cancelling = true;
        return;
      }
      await this.postLifecycleAction(item.action);
    },

    closeLifecycleCancel() {
      const view = this.lifecycle;
      if (view.busy) return;
      view.cancelling = false;
      view.reason = "";
      view.reasonError = "";
    },

    async submitLifecycleCancel() {
      const view = this.lifecycle;
      const reason = String(view.reason ?? "").trim();
      if (!reason) {
        view.reasonError = "Enter a reason for cancelling this event.";
        return;
      }
      view.reasonError = "";
      const done = await this.postLifecycleAction("cancel", { reason });
      if (done) {
        view.cancelling = false;
        view.reason = "";
      }
    },

    async postLifecycleAction(action, body) {
      const view = this.lifecycle;
      if (view.busy) return false;
      view.busy = true;
      view.error = "";
      const dialog = this.eventDetail;
      try {
        const response = await this.apiRequest(`/events/${dialog.id}/${action}`, { method: "POST", body, inlineErrors: true });
        if (!response.ok) {
          view.error = typeof response.data?.detail === "string" ? response.data.detail : "This change could not be made. Please try again.";
          return false;
        }
        dialog.detail.status = response.data.status;
        view.notice = response.data.message;
        await this.loadLifecycleActions();
        await this.refreshListsAfterLifecycle();
        return true;
      } finally {
        view.busy = false;
      }
    },

    // The list behind the dialog shows each event's status, so it reads again too.
    async refreshListsAfterLifecycle() {
      const role = this.normalizeRole(this.user?.role);
      if (role === "Organiser") await this.fetchEvents();
      else if (role === "Coordinator") {
        const tasks = [this.fetchAssignedEvents()];
        if (this.currentPath === "/coordinator/all-events" || this.allCoordinatorEvents?.length) tasks.push(this.fetchAllCoordinatorEvents());
        await Promise.all(tasks);
      }
      else if (role === "Event Coordinator Lead") await this.fetchLeadData();
    },
  };
}
