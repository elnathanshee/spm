// [SCRUM-48, SCRUM-65] The registrations panel in the event detail dialog: the
// setting toggle and the list, for the event's Organiser and assigned Coordinator.

function detailOf(response, fallback) {
  const detail = response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export function eventRegistrations() {
  return {
    regSetting: null,
    regList: null,
    regLoaded: false,
    regLoading: false,
    regBusy: false,
    regError: "",

    init() {
      if (!this.canManageRegistrations()) return undefined;
      return this.loadEventRegistrations();
    },

    // The backend decides. This only keeps the panel away from people it would refuse.
    canManageRegistrations() {
      const detail = this.eventDetail?.detail;
      const me = Number(this.user?.user_id);
      if (!detail) return false;
      if (this.user?.role === "Organiser") return Number(detail.organiser_id) === me;
      if (this.user?.role === "Coordinator") return detail.assigned_coordinator_id != null && Number(detail.assigned_coordinator_id) === me;
      return false;
    },

    async loadEventRegistrations() {
      const eventId = this.eventDetail.id;
      this.regLoading = true;
      this.regError = "";
      const [setting, list] = await Promise.all([
        this.apiRequest(`/events/${eventId}/registration-settings`, { inlineErrors: true }),
        this.apiRequest(`/events/${eventId}/registrations`, { inlineErrors: true }),
      ]);
      this.regLoading = false;
      if (setting.ok && list.ok) {
        this.regSetting = setting.data;
        this.regList = list.data;
        this.regLoaded = true;
        return;
      }
      this.regError = detailOf(setting.ok ? list : setting, "We couldn't load the registrations. Please try again.");
    },

    async toggleRegistration() {
      if (this.regBusy) return;
      this.regBusy = true;
      this.regError = "";
      const response = await this.apiRequest(`/events/${this.eventDetail.id}/registration-settings`, {
        method: "PUT",
        body: { enabled: !this.regSetting.enabled },
        inlineErrors: true,
      });
      this.regBusy = false;
      if (response.ok) this.regSetting = response.data;
      else this.regError = detailOf(response, "We couldn't change the setting. Please try again.");
    },

    regToggleLabel() {
      return this.regSetting?.enabled ? "Turn registration off" : "Turn registration on";
    },

    regCounts() {
      const list = this.regList;
      if (list.capacity === null) return "The number of places isn't known yet: it needs an expected attendance or a Confirmed venue.";
      return `${list.registered_count} registered of ${list.capacity} places, ${list.places_left} left.`;
    },

    regRowStatus(row) {
      return row.status === "registered" ? "Registered" : "Withdrawn";
    },

    regRowClass(row) {
      return row.status === "registered" ? "badge-success" : "badge-muted";
    },

    // Booking and registration times are stored in UTC.
    regRowTime(row) {
      return this.formatUtc(row.withdrawn_at || row.registered_at);
    },
  };
}
