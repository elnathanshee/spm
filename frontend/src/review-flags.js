// [SCRUM-87] Arrangements marked for another look, in the event detail dialog. The backend
// decides who may read and clear them; these checks only avoid asking when the answer is no.
// Uses the parent scope: apiRequest, eventDetail, user.
export function reviewFlags() {
  return {
    flags: { items: [], busy: false, error: "", notice: "" },

    async init() {
      await this.loadReviewFlags();
    },

    canSeeFlags() {
      const role = this.normalizeRole(this.user?.role);
      if (role === "Organiser") return Number(this.eventDetail?.detail?.organiser_id) === Number(this.user?.user_id);
      return this.canClearFlags();
    },

    canClearFlags() {
      const role = this.normalizeRole(this.user?.role);
      if (role === "Event Coordinator Lead") return true;
      return role === "Coordinator" && Number(this.eventDetail?.detail?.assigned_coordinator_id) === Number(this.user?.user_id);
    },

    async loadReviewFlags() {
      if (!this.eventDetail || !this.canSeeFlags()) return;
      const response = await this.apiRequest(`/events/${this.eventDetail.id}/review-flags`, { inlineErrors: true });
      if (response.ok) this.flags.items = response.data;
      else this.flags.error = response.data?.detail || "The review flags could not be loaded.";
    },

    flagKindLabel(flag) {
      return flag.kind === "equipment" ? "Equipment" : "Venue";
    },

    flagSourceLabel(flag) {
      return flag.source === "important_change" ? "Approved change" : "Safety check";
    },

    async clearReviewFlags() {
      const view = this.flags;
      if (view.busy) return;
      view.busy = true;
      view.error = "";
      try {
        const response = await this.apiRequest(`/events/${this.eventDetail.id}/review-flags/clear`, { method: "POST", inlineErrors: true });
        if (response.ok) {
          view.items = [];
          view.notice = "Marked as looked at. The record of these flags is kept.";
        } else {
          view.error = response.data?.detail || "The review flags could not be cleared. Please try again.";
        }
      } finally {
        view.busy = false;
      }
    },
  };
}
