// [SCRUM-45, 46, 47] The Attendee's Event directory: open events, register, my
// registrations and withdraw with a confirm step. The backend only ever sends this
// screen the whitelisted fields, so there is nothing internal here to hide.

function detailOf(response, fallback) {
  const detail = response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export function attendeeRegistration() {
  return {
    regEvents: [],
    regMine: [],
    regLoaded: false,
    regLoading: false,
    regError: "",
    regNotice: "",
    regBusyId: null,
    regConfirmId: null,

    init() {
      return this.loadRegistrationData();
    },

    async loadRegistrationData() {
      this.regLoading = true;
      this.regError = "";
      const [open, mine] = await Promise.all([
        this.apiRequest("/registrations/events", { inlineErrors: true }),
        this.apiRequest("/registrations/mine", { inlineErrors: true }),
      ]);
      this.regLoading = false;
      if (open.ok && mine.ok) {
        this.regEvents = open.data;
        this.regMine = mine.data;
        this.regLoaded = true;
        return;
      }
      this.regError = detailOf(open.ok ? mine : open, "We couldn't load the events. Please try again.");
    },

    // Register and withdraw share this: send, say what happened, and reload the
    // places either way, because a refusal usually means the list was out of date.
    async sendRegistrationAction(eventId, path, fallback) {
      this.regBusyId = eventId;
      this.regNotice = "";
      this.regError = "";
      const response = await this.apiRequest(path, { method: "POST", inlineErrors: true });
      await this.loadRegistrationData();
      this.regBusyId = null;
      if (response.ok) this.regNotice = response.data?.message || "";
      else this.regError = detailOf(response, fallback);
      // The button that was pressed may be gone now, so focus moves to the message.
      this.$nextTick?.(() => this.$refs?.regStatus?.focus());
    },

    registerFor(event) {
      if (this.regBusyId !== null) return Promise.resolve();
      return this.sendRegistrationAction(event.event_id, `/registrations/events/${event.event_id}`, "We couldn't register you. Please try again.");
    },

    askWithdraw(item) {
      this.regConfirmId = item.event_id;
    },

    keepPlace() {
      this.regConfirmId = null;
    },

    confirmWithdraw(item) {
      if (this.regBusyId !== null) return Promise.resolve();
      this.regConfirmId = null;
      return this.sendRegistrationAction(item.event_id, `/registrations/events/${item.event_id}/withdraw`, "We couldn't withdraw you. Please try again.");
    },

    regWhen(event) {
      return this.formatDate(event.proposed_start);
    },

    regVenues(event) {
      if (!event.venues.length) return "Venue to be confirmed";
      return event.venues.map(venue => `${venue.name}, ${venue.location}`).join("; ");
    },

    regPlaces(event) {
      if (event.places_left === 0) return "Full";
      return event.places_left === 1 ? "1 place left" : `${event.places_left} places left`;
    },

    regCanRegister(event) {
      return event.places_left > 0 && event.my_status !== "registered";
    },

    regRegisterLabel(event) {
      return event.my_status === "withdrawn" ? "Register again" : "Register";
    },

    regCanWithdraw(item) {
      return item.my_status === "registered" && item.event_state !== "cancelled" && item.event_state !== "completed";
    },

    regStatusText(item) {
      if (item.my_status === "withdrawn") return "Withdrawn";
      if (item.event_state === "cancelled") return "Event cancelled";
      if (item.event_state === "completed") return "Event completed";
      return "Registered";
    },

    regStatusClass(item) {
      if (item.my_status === "withdrawn" || item.event_state === "completed") return "badge-muted";
      return item.event_state === "cancelled" ? "badge-danger" : "badge-success";
    },
  };
}
