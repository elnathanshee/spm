// [SCRUM-49, SCRUM-50] The change requests panel in the event detail dialog.
// The Organiser asks for a change with a reason; the assigned Coordinator decides.
// Every name here starts with "change" because Alpine merges this component's
// scope with the page's, and a plain "busy" could hide the page's own.
import { normalizeRole } from "./rbac.js";

// [SCRUM-29] The same split as SIGNIFICANT_FIELDS in events.py: only description is routine.
const CHANGE_FIELDS = [
  { key: "proposed_start", label: "Date and time", kind: "Important" },
  { key: "expected_attendance", label: "Expected attendance", kind: "Important" },
  { key: "venue_requirements", label: "Venue requirements", kind: "Important" },
  { key: "equipment_requirements", label: "Equipment requirements", kind: "Important" },
  { key: "description", label: "Description", kind: "Routine" },
];

// The statuses change_requests.CHANGEABLE_STATUSES accepts.
const ASKABLE_STATUSES = ["Submitted", "Under Review", "Approved", "Planning", "Safety Review", "Confirmed"];

function emptyText(value) {
  return value === null || value === undefined ? "" : String(value);
}

// A stored date, as a datetime-local input wants it (no seconds).
function dateInputValue(value) {
  return emptyText(value).slice(0, 16);
}

function formFrom(detail) {
  return {
    proposed_start: dateInputValue(detail?.proposed_start),
    expected_attendance: emptyText(detail?.expected_attendance),
    venue_requirements: emptyText(detail?.venue_requirements),
    equipment_requirements: emptyText(detail?.equipment_requirements),
    description: emptyText(detail?.description),
    reason: "",
  };
}

function flaggedText(count) {
  if (!count) return "No arrangements were flagged.";
  return `${count} ${count === 1 ? "arrangement" : "arrangements"} flagged for another look.`;
}

export function changeRequests() {
  return {
    changeItems: [],
    changeLoading: false,
    changeLoadError: "",
    changeForm: formFrom(null),
    changeError: "",
    changeNotice: "",
    changeBusy: false,
    changeDecisionReason: "",
    changeDecisionError: "",

    // Alpine runs init() when the panel appears in the dialog.
    async init() {
      this.changeForm = formFrom(this.eventDetail?.detail);
      if (this.panelShown()) await this.loadChanges();
    },

    canAsk() {
      const detail = this.eventDetail?.detail;
      return !!detail && normalizeRole(this.user?.role) === "Organiser" && detail.organiser_id === this.user?.user_id;
    },
    canDecide() {
      const detail = this.eventDetail?.detail;
      return !!detail && normalizeRole(this.user?.role) === "Coordinator" && detail.assigned_coordinator_id === this.user?.user_id;
    },
    panelShown() {
      return this.canAsk() || this.canDecide();
    },
    pendingItems() {
      return this.changeItems.filter(item => item.status === "Pending");
    },

    // Why the Organiser can't ask right now, or "" when they can. The server checks the same things.
    askBlocked() {
      const detail = this.eventDetail?.detail;
      if (!detail) return "";
      if (!ASKABLE_STATUSES.includes(detail.status)) return `A change request can't be raised while this event is ${detail.status}.`;
      if (!detail.assigned_coordinator_id) return "A change request can be raised once a Coordinator has been assigned to this event.";
      if (this.pendingItems().length) return "Your change request is waiting for the Coordinator's decision. You can raise another once it's decided.";
      return "";
    },

    // One line per field of a request, for the list both people read.
    changeLines(item) {
      const shown = (key, value) => {
        if (value === null || value === undefined || value === "") return "Not set";
        return key === "proposed_start" ? this.formatDate(value) : String(value);
      };
      return CHANGE_FIELDS.filter(field => field.key in (item.changes || {})).map(field => ({
        key: field.key,
        label: field.label,
        kind: field.kind,
        from: shown(field.key, item.previous_values?.[field.key]),
        to: shown(field.key, item.changes[field.key]),
      }));
    },
    outcomeText(item) {
      if (item.status === "Pending") return "Waiting for the Coordinator's decision.";
      return `${item.status} by ${item.decided_by}: ${item.decision_reason}`;
    },

    async loadChanges() {
      const view = this.eventDetail;
      this.changeLoading = true;
      this.changeLoadError = "";
      const response = await this.apiRequest(`/events/${view.id}/change-requests`, { inlineErrors: true });
      if (this.eventDetail !== view) return;
      this.changeLoading = false;
      if (response.ok) this.changeItems = response.data;
      else if (response.status !== 401) this.changeLoadError = `Couldn't load the change requests: ${this.dialogErrorMessage(response)}`;
    },

    // Only the fields the Organiser really changed, or an error sentence.
    changesToSend() {
      const form = this.changeForm;
      const start = formFrom(this.eventDetail?.detail);
      const changes = {};
      for (const { key } of CHANGE_FIELDS) {
        if (form[key].trim() !== start[key].trim()) changes[key] = form[key].trim();
      }
      if ("proposed_start" in changes && !changes.proposed_start) return { error: "Enter a date and time, or put the current one back." };
      if ("expected_attendance" in changes) {
        if (!/^\d+$/.test(changes.expected_attendance) || Number(changes.expected_attendance) < 1) {
          return { error: "Expected attendance must be a whole number, 1 or more." };
        }
        changes.expected_attendance = Number(changes.expected_attendance);
      }
      if (!Object.keys(changes).length) return { error: "Change at least one field before sending the request." };
      if (!form.reason.trim()) return { error: "Please give a reason for the change." };
      return { changes };
    },

    async submitChange() {
      const view = this.eventDetail;
      if (this.changeBusy || !this.canAsk()) return;
      this.changeError = "";
      this.changeNotice = "";
      const { changes, error } = this.changesToSend();
      if (error) { this.changeError = error; return; }
      this.changeBusy = true;
      try {
        const response = await this.apiRequest(`/events/${view.id}/change-requests`, {
          method: "POST",
          body: { changes, reason: this.changeForm.reason.trim() },
          inlineErrors: true,
        });
        if (this.eventDetail !== view) return;
        if (response.ok) {
          this.changeForm = formFrom(view.detail);
          this.changeNotice = "Your change request was sent to the Coordinator. The event stays as it is until they decide.";
          await this.loadChanges();
        } else if (response.status !== 401) {
          this.changeError = this.dialogErrorMessage(response);
        }
      } finally {
        this.changeBusy = false;
      }
    },

    async decideChange(item, decision) {
      const view = this.eventDetail;
      if (this.changeBusy || !this.canDecide()) return;
      this.changeDecisionError = "";
      this.changeNotice = "";
      const reason = this.changeDecisionReason.trim();
      if (!reason) { this.changeDecisionError = "Please give a reason for your decision."; return; }
      this.changeBusy = true;
      try {
        const response = await this.apiRequest(`/events/${view.id}/change-requests/${item.id}/decision`, {
          method: "POST",
          body: { decision, reason },
          inlineErrors: true,
        });
        if (this.eventDetail !== view) return;
        if (!response.ok) {
          if (response.status !== 401) this.changeDecisionError = this.dialogErrorMessage(response);
          return;
        }
        this.changeDecisionReason = "";
        this.changeNotice = decision === "approve"
          ? `Approved and applied. ${flaggedText(response.data.arrangements_flagged)}`
          : "Rejected. The event hasn't changed.";
        await this.loadChanges();
        if (decision === "approve") await this.refreshEvent(view);
        await this.fetchAssignedEvents();
        // The buttons have gone, so focus moves to the panel's heading rather than being lost.
        if (typeof this.$nextTick === "function") this.$nextTick(() => document.getElementById("change-requests-title")?.focus());
      } finally {
        this.changeBusy = false;
      }
    },

    // Approval changed the event, so the dialog shows the new values.
    async refreshEvent(view) {
      const response = await this.apiRequest(`/events/${view.id}`, { inlineErrors: true });
      if (response.ok && this.eventDetail === view) Object.assign(view.detail, response.data);
    },
  };
}
