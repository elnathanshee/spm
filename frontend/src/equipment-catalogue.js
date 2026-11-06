// [Sprint 5 | SCRUM-41, SCRUM-42] The equipment catalogue on /technical: list, add and
// edit items, and record units unavailable for a period. Its state sits under one
// "catalogue" key because Alpine merges scopes: a child's "form" or "error" would
// shadow the parent page's own when its helpers run.

const MAX_QUANTITY = 2147483647;
const NO_CHANGES = "No changes to save.";

const emptyItemForm = () => ({ equipment_type: "", name: "", total_quantity: "", notes: "" });
const emptyPeriodForm = () => ({ units: "", start_at: "", end_at: "", reason: "" });
const text = value => String(value ?? "").trim();

// "12" is 12. Anything that isn't a whole number from 1 to the column limit is null.
function quantityFrom(value) {
  const typed = text(value);
  if (!/^\d+$/.test(typed)) return null;
  const number = Number(typed);
  return number >= 1 && number <= MAX_QUANTITY ? number : null;
}

// The backend's rules, checked first so the user gets every message at once.
function itemErrors(form) {
  const errors = {};
  const type = text(form.equipment_type);
  const name = text(form.name);
  if (!type) errors.equipment_type = "Enter the equipment type.";
  else if (type.length > 100) errors.equipment_type = "Keep the type to 100 characters.";
  if (!name) errors.name = "Enter the item's name.";
  else if (name.length > 255) errors.name = "Keep the name to 255 characters.";
  if (quantityFrom(form.total_quantity) === null) errors.total_quantity = "Enter a whole number, 1 or more.";
  return errors;
}

function itemBody(form) {
  return {
    equipment_type: text(form.equipment_type),
    name: text(form.name),
    total_quantity: quantityFrom(form.total_quantity),
    notes: text(form.notes) || null,
  };
}

export function equipmentCatalogue() {
  return {
    catalogue: {
      items: [], loaded: false, busy: false, error: "", denied: false, message: "",
      form: null, editing: null, errors: {}, saving: false,
      periods: null,
    },

    init() { return this.loadCatalogue(); },

    async loadCatalogue() {
      const view = this.catalogue;
      view.busy = true;
      const response = await this.apiRequest("/equipment/items", { inlineErrors: true });
      view.busy = false;
      if (response.ok) {
        view.items = response.data.items;
        Object.assign(view, { loaded: true, error: "", denied: false });
      } else if (response.status !== 401) {
        // A failed refresh keeps the list shown and says so.
        Object.assign(view, { denied: response.status === 403, error: this.dialogErrorMessage(response) });
      }
    },

    startCatalogueAdd() {
      Object.assign(this.catalogue, { form: emptyItemForm(), editing: null, errors: {}, message: "", periods: null });
    },

    startCatalogueEdit(item) {
      const form = { equipment_type: item.equipment_type, name: item.name, total_quantity: String(item.total_quantity), notes: item.notes || "" };
      Object.assign(this.catalogue, { form, editing: item, errors: {}, message: "", periods: null });
    },

    cancelCatalogueForm() {
      Object.assign(this.catalogue, { form: null, editing: null, errors: {} });
      // The form held the focus, so hand it back once Add item is showing again.
      this.$nextTick?.(() => this.$refs?.catalogueAdd?.focus());
    },

    async saveCatalogueItem() {
      const view = this.catalogue;
      view.errors = itemErrors(view.form);
      if (Object.keys(view.errors).length) return;
      const body = itemBody(view.form);
      let path = "/equipment/items";
      let method = "POST";
      let sending = body;
      if (view.editing) {
        // [SCRUM-42] Only what changed, so a stale form can't undo someone else's edit.
        const changed = Object.entries(body).filter(([field, value]) => value !== view.editing[field]);
        if (!changed.length) { view.errors = { form: NO_CHANGES }; return; }
        path = `/equipment/items/${view.editing.id}`;
        method = "PATCH";
        sending = Object.fromEntries(changed);
      }
      view.saving = true;
      const response = await this.apiRequest(path, { method, inlineErrors: true, body: sending });
      view.saving = false;
      if (!response.ok) {
        if (response.status !== 401) view.errors = { form: this.dialogErrorMessage(response) };
        return;
      }
      const verb = view.editing ? "Updated" : "Added";
      this.cancelCatalogueForm();
      view.message = `${verb} ${body.name}.`;
      await this.loadCatalogue();
    },

    // ---- [SCRUM-42] Units unavailable for a period
    async openCataloguePeriods(item) {
      Object.assign(this.catalogue, { form: null, editing: null, errors: {}, message: "" });
      this.catalogue.periods = { item, rows: [], form: emptyPeriodForm(), errors: {}, message: "", saving: false };
      await this.loadPeriods();
    },

    closeCataloguePeriods() { this.catalogue.periods = null; },

    async loadPeriods() {
      const periods = this.catalogue.periods;
      const response = await this.apiRequest(`/equipment/items/${periods.item.id}/unavailability`, { inlineErrors: true });
      if (response.ok) periods.rows = response.data.periods;
      else if (response.status !== 401) periods.errors = { form: this.dialogErrorMessage(response) };
    },

    periodErrors(form, item) {
      const errors = {};
      const units = quantityFrom(form.units);
      if (units === null) errors.units = "Enter a whole number, 1 or more.";
      else if (units > item.total_quantity) errors.units = `This item only has ${item.total_quantity} units in total.`;
      const start = this.inputToDate(form.start_at);
      const end = this.inputToDate(form.end_at);
      if (!start) errors.start = "Choose when the units become unavailable.";
      if (!end) errors.end = "Choose when the units are back.";
      else if (start && end <= start) errors.end = "The end must be after the start.";
      return errors;
    },

    async savePeriod() {
      const periods = this.catalogue.periods;
      periods.errors = this.periodErrors(periods.form, periods.item);
      periods.message = "";
      if (Object.keys(periods.errors).length) return;
      periods.saving = true;
      const response = await this.apiRequest(`/equipment/items/${periods.item.id}/unavailability`, {
        method: "POST",
        inlineErrors: true,
        body: {
          units: quantityFrom(periods.form.units),
          // The input is Singapore time, like every time the app shows.
          start_at: this.inputToDate(periods.form.start_at).toISOString(),
          end_at: this.inputToDate(periods.form.end_at).toISOString(),
          reason: text(periods.form.reason) || null,
        },
      });
      periods.saving = false;
      if (!response.ok) {
        if (response.status !== 401) periods.errors = { form: this.dialogErrorMessage(response) };
        return;
      }
      periods.form = emptyPeriodForm();
      periods.message = "Unavailable units recorded.";
      await this.loadPeriods();
    },

    async removePeriod(period) {
      const periods = this.catalogue.periods;
      periods.errors = {};
      periods.message = "";
      const response = await this.apiRequest(`/equipment/items/${periods.item.id}/unavailability/${period.id}`, { method: "DELETE", inlineErrors: true });
      if (!response.ok) {
        if (response.status !== 401) periods.errors = { form: this.dialogErrorMessage(response) };
        return;
      }
      periods.message = "Unavailable period removed.";
      await this.loadPeriods();
    },
  };
}
