// [Sprint 5 | SCRUM-40, SCRUM-43, SCRUM-44] Equipment arrangements.
// equipmentRequestActions: one per worklist row. Technical Support set the status and
// note, see what's available for the event, reserve and release.
// eventReservations: the read-only panel in the event details.
// State sits under one key because Alpine merges scopes: a child's "error" or "status"
// would shadow the parent page's own when its helpers run.

const MAX_QUANTITY = 2147483647;
const text = value => String(value ?? "").trim();
const sameWords = (a, b) => text(a).toLowerCase() === text(b).toLowerCase();

function wholeQuantity(value) {
  const typed = text(value);
  if (!/^\d+$/.test(typed)) return null;
  const number = Number(typed);
  return number >= 1 && number <= MAX_QUANTITY ? number : null;
}

// One badge tone per equipment status, from the tones the event statuses use.
const STATUS_TONES = { requested: "waiting", reserved: "info", arranged: "success", unavailable: "danger" };

export function equipmentRequestActions() {
  return {
    arrangement: {
      status: "requested", note: "", busy: false, error: "", message: "",
      reserveOpen: false, availability: [], availabilityLoaded: false, reservations: [],
      itemId: "", quantity: "", reserveBusy: false, reserveError: "",
    },

    startArrangement(item) { this.arrangement.status = item.latest_status || "requested"; },

    arrangementTone(status) { return `badge-${STATUS_TONES[status] || "neutral"}`; },

    // ---- [SCRUM-40] Status and note
    async saveArrangementStatus(item) {
      const view = this.arrangement;
      Object.assign(view, { busy: true, error: "", message: "" });
      const response = await this.apiRequest(`/equipment-requests/${item.id}/status`, {
        method: "POST",
        inlineErrors: true,
        body: { status: view.status, note: text(view.note) || null },
      });
      view.busy = false;
      if (!response.ok) {
        if (response.status !== 401) view.error = this.dialogErrorMessage(response);
        return;
      }
      item.latest_status = response.data.equipment_request.latest_status;
      item.latest_note = response.data.equipment_request.latest_note;
      view.note = "";
      view.message = `Status updated to ${item.latest_status}.`;
    },

    // ---- [SCRUM-43] What's available for the event's window, and what it already holds
    async toggleReserve(item) {
      const view = this.arrangement;
      view.reserveOpen = !view.reserveOpen;
      if (!view.reserveOpen) return;
      Object.assign(view, { reserveError: "", message: "", quantity: String(item.quantity), itemId: "" });
      await this.loadReserveData(item);
      // Offer the catalogue item the request names, if there is one. The user can change it.
      const match = view.availability.find(line => sameWords(line.name, item.equipment_type) || sameWords(line.equipment_type, item.equipment_type));
      if (match) view.itemId = String(match.item_id);
    },

    async loadReserveData(item) {
      const view = this.arrangement;
      const [available, held] = [
        await this.apiRequest(`/equipment/availability?event_id=${item.event_id}`, { inlineErrors: true }),
        await this.apiRequest(`/events/${item.event_id}/equipment-reservations`, { inlineErrors: true }),
      ];
      if (available.ok) Object.assign(view, { availability: available.data.items, availabilityLoaded: true });
      else if (available.status !== 401) Object.assign(view, { availability: [], availabilityLoaded: false, reserveError: this.dialogErrorMessage(available) });
      if (held.ok) view.reservations = held.data.reservations;
    },

    availabilityLabel(line) {
      return `${line.name} (${line.equipment_type}): ${line.available} available of ${line.total_quantity}, ${line.reserved} reserved, ${line.unavailable} unavailable`;
    },

    chosenAvailability() {
      const line = this.arrangement.availability.find(row => String(row.item_id) === String(this.arrangement.itemId));
      return line ? this.availabilityLabel(line) : "";
    },

    // A reservation follows its event, so moving an event can leave more held than exists.
    overCommittedLines() { return this.arrangement.availability.filter(line => line.over_committed); },

    overCommittedLabel(line) {
      return `${line.name}: ${line.reserved + line.unavailable} units are reserved or unavailable for this event's date and time, but there are only ${line.total_quantity}. Release some or find more.`;
    },

    // ---- [SCRUM-44] Reserve and release
    heldLabel(held) {
      return `${held.quantity} of ${held.item_name}${held.released_at ? " (released)" : ""}`;
    },

    reserveProblem() {
      const view = this.arrangement;
      const line = view.availability.find(row => String(row.item_id) === String(view.itemId));
      if (!line) return "Choose an item from the catalogue.";
      const quantity = wholeQuantity(view.quantity);
      if (quantity === null) return "Enter a whole number, 1 or more.";
      if (quantity > line.available) return `Only ${line.available} of ${line.name} available for this event's date and time.`;
      return "";
    },

    async reserveForRequest(item) {
      const view = this.arrangement;
      view.message = "";
      view.reserveError = this.reserveProblem();
      if (view.reserveError) return;
      const line = view.availability.find(row => String(row.item_id) === String(view.itemId));
      const quantity = wholeQuantity(view.quantity);
      view.reserveBusy = true;
      const response = await this.apiRequest(`/events/${item.event_id}/equipment-reservations`, {
        method: "POST",
        inlineErrors: true,
        body: { item_id: line.item_id, quantity },
      });
      view.reserveBusy = false;
      if (!response.ok && response.status === 401) return;
      // Reloaded either way: a refusal means someone else took the units first.
      const refusal = response.ok ? "" : this.dialogErrorMessage(response);
      await this.loadReserveData(item);
      if (refusal) { view.reserveError = refusal; return; }
      view.message = `Reserved ${quantity} of ${line.name} for ${item.event_title}. Update the status when it's confirmed.`;
    },

    async releaseReservation(item, reservation) {
      const view = this.arrangement;
      // Looked up first: Alpine ties $refs to the clicked button, which may be about to go.
      const reserveButton = this.$refs?.reserveToggle;
      Object.assign(view, { reserveError: "", message: "", reserveBusy: true });
      const response = await this.apiRequest(`/events/${item.event_id}/equipment-reservations/${reservation.id}`, { method: "DELETE", inlineErrors: true });
      view.reserveBusy = false;
      if (!response.ok && response.status === 401) return;
      // Reloaded either way: another line on this event may have released it first.
      const refusal = response.ok ? "" : this.dialogErrorMessage(response);
      await this.loadReserveData(item);
      // The Release button has gone, so hand the focus to the row's Reserve button.
      this.$nextTick?.(() => reserveButton?.focus());
      if (refusal) { view.reserveError = refusal; return; }
      view.message = `Released ${reservation.quantity} of ${reservation.item_name}.`;
    },
  };
}

export function eventReservations() {
  return {
    reservationPanel: { rows: [], loaded: false, error: "" },

    async init() {
      const panel = this.reservationPanel;
      const response = await this.apiRequest(`/events/${this.eventDetail.id}/equipment-reservations`, { inlineErrors: true });
      if (response.ok) Object.assign(panel, { rows: response.data.reservations, loaded: true, error: "" });
      else if (response.status !== 401) panel.error = this.dialogErrorMessage(response);
    },

    reservationState(reservation) {
      return reservation.released_at ? `Released ${this.formatUtc(reservation.released_at)}` : "Reserved";
    },
  };
}
