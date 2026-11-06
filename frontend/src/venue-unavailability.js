// [SCRUM-82] The unavailability panel in the Venue Staff venue record: list a
// venue's periods, record one, and remove one. The inputs are Singapore time, like
// every time the app shows; the backend stores them in UTC.

const SINGAPORE_OFFSET = "+08:00";

const emptyForm = () => ({ start_at: "", end_at: "", reason: "" });

// A datetime-local value with its offset, or null if it isn't one.
function withOffset(value) {
  const text = String(value || "");
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text) ? `${text}:00${SINGAPORE_OFFSET}` : null;
}

// One sentence from a refused request: a 422 lists its problems, others have a detail.
function refusalText(response, fallback) {
  const detail = response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(item => String(item.msg || "").replace(/^Value error, /, "")).join(" ");
  return fallback;
}

export function venueUnavailability(venue) {
  return {
    venue,
    periods: [],
    loading: false,
    loadError: "",
    form: emptyForm(),
    errors: {},
    busy: false,
    removingId: null,
    message: null,
    affected: [],

    async load() {
      this.loading = true;
      this.loadError = "";
      try {
        const response = await this.apiRequest(`/venues/${this.venue.id}/unavailability`, { inlineErrors: true });
        if (response.ok && Array.isArray(response.data)) this.periods = response.data;
        else this.loadError = "The unavailable periods could not be loaded. Try again shortly.";
      } finally {
        this.loading = false;
      }
    },

    validate() {
      const errors = {};
      const start = withOffset(this.form.start_at);
      const end = withOffset(this.form.end_at);
      if (!start) errors.start = "Enter a start date and time";
      if (!end) errors.end = "Enter an end date and time";
      else if (start && new Date(end) <= new Date(start)) errors.end = "The end must be after the start";
      if (!String(this.form.reason || "").trim()) errors.reason = "Enter a reason, such as maintenance or safety";
      return errors;
    },

    async save() {
      if (this.busy) return;
      this.errors = this.validate();
      if (Object.keys(this.errors).length) return;
      this.busy = true;
      this.message = null;
      this.affected = [];
      try {
        const response = await this.apiRequest(`/venues/${this.venue.id}/unavailability`, {
          method: "POST",
          inlineErrors: true,
          body: {
            start_at: withOffset(this.form.start_at),
            end_at: withOffset(this.form.end_at),
            reason: this.form.reason.trim(),
          },
        });
        if (!response.ok) {
          this.message = { kind: "error", text: refusalText(response, "The period could not be saved. Try again.") };
          return;
        }
        this.form = emptyForm();
        this.affected = response.data?.affected_bookings || [];
        this.message = { kind: "info", text: this.savedText(this.affected.length) };
        await this.load();
        await this.fetchVenueWorkspace();  // the booking lists show the new flags
      } finally {
        this.busy = false;
      }
    },

    savedText(count) {
      if (!count) return "Period recorded. No bookings are affected.";
      const bookings = count === 1 ? "1 booking is flagged" : `${count} bookings are flagged`;
      return `Period recorded. ${bookings} and nothing is cancelled. Assigned Coordinators have been told.`;
    },

    async remove(period) {
      if (this.removingId !== null) return;
      this.removingId = period.id;
      this.message = null;
      this.affected = [];
      try {
        const response = await this.apiRequest(`/venues/${this.venue.id}/unavailability/${period.id}`, { method: "DELETE", inlineErrors: true });
        if (!response.ok) {
          this.message = { kind: "error", text: refusalText(response, "The period could not be removed. Try again.") };
          return;
        }
        this.message = { kind: "info", text: "Period removed. Its flags have cleared." };
        await this.load();
        await this.fetchVenueWorkspace();
        // The button pressed has gone, so keep focus in the panel.
        this.$refs?.unavailabilityHeading?.focus?.();
      } finally {
        this.removingId = null;
      }
    },
  };
}
