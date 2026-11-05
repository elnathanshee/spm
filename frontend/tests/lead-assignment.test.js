import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app(document = { addEventListener: (_, fn) => fn() }) {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 9, username: "lead1", role: "Event Coordinator Lead" };
  state.coordinators = [{ id: 2, username: "coord1" }, { id: 3, username: "coord2" }];
  return state;
}
// Rows as GET /lead/events returns them
const row = (id, status, extra = {}) => ({ id, title: `Event ${id}`, organiser: "org1", proposed_start: "2099-01-01T09:00:00", expected_attendance: 20, status, assigned_coordinator_id: null, assigned_coordinator: null, submitted_at: "2026-10-01T00:00:00", ...extra });

test("TC-9951 Save is enabled only when the choice differs from the current assignment", () => {
  const state = app();
  assert.equal(state.assignmentChanged({ assigned_coordinator_id: null, selectedCoordinatorId: "" }), false, "nothing chosen");
  assert.equal(state.assignmentChanged({ assigned_coordinator_id: null, selectedCoordinatorId: "2" }), true);
  assert.equal(state.assignmentChanged({ assigned_coordinator_id: 2, selectedCoordinatorId: "2" }), false, "same Coordinator: the backend would refuse it");
  assert.equal(state.assignmentChanged({ assigned_coordinator_id: 2, selectedCoordinatorId: 3 }), true);
  const start = html.indexOf('x-for="event in filteredLeadEvents()"');
  const rowHtml = html.slice(start, html.indexOf("</tr></template>", start));
  // Changed on purpose: the save button no longer shows disabled; it's hidden until there's something to save.
  assert.match(rowHtml, /<div class="button-group lead-assign-actions" :class="assignmentActionsShown\(event\) \? '' : 'is-hidden'" :aria-hidden="!assignmentActionsShown\(event\)">/);
  assert.match(rowHtml, /:id="`assign-save-\$\{event\.id\}`" :disabled="!!rowAssignment\(event\)\.busy"/);
});

test("TC-9952 saving shows success on the row and as a toast; a refusal shows on the row only", async () => {
  const state = app();
  const event = row(8, "Submitted", { selectedCoordinatorId: "2" });
  state.leadEvents = [event];
  const sent = [];
  let refreshed = 0;
  state.fetchLeadData = async () => { refreshed += 1; };
  state.apiRequest = async (path, options) => { sent.push([path, options.body.coordinator_id, options.inlineErrors]); return { ok: true, status: 200, data: {} }; };
  await state.saveAssignment(event);
  assert.deepEqual(sent, [["/events/8/assign", 2, true]]);
  assert.equal(refreshed, 1);
  assert.equal(state.rowAssignment(event).message, "Assigned to coord1.");
  assert.equal(state.alert.message, "Event request #8 assigned to coord1.");

  const assigned = row(9, "Under Review", { assigned_coordinator_id: 2, assigned_coordinator: "coord1", selectedCoordinatorId: "3" });
  state.alert = null;
  state.apiRequest = async () => ({ ok: false, status: 400, data: { detail: "Only active submitted event requests can be assigned" } });
  await state.saveAssignment(assigned);
  assert.equal(state.rowAssignment(assigned).error, "Only active submitted event requests can be assigned");
  assert.equal(state.alert, null, "the error isn't also a page alert");

  // Unchanged: nothing is sent
  sent.length = 0;
  state.apiRequest = async path => { sent.push(path); return { ok: true }; };
  await state.saveAssignment({ ...assigned, selectedCoordinatorId: "2" });
  assert.deepEqual(sent, []);
});

test("TC-9953 a choice not saved yet survives the 15-second refresh, unless the assignment changed meanwhile", async () => {
  const state = app();
  const respond = rows => async path => path === "/lead/events" ? { ok: true, status: 200, data: rows } : { ok: true, status: 200, data: state.coordinators };
  state.apiRequest = respond([row(1, "Submitted"), row(2, "Submitted", { assigned_coordinator_id: 2, assigned_coordinator: "coord1" })]);
  await state.fetchLeadData();
  state.leadEvents[0].selectedCoordinatorId = "3";
  state.leadEvents[1].selectedCoordinatorId = "3";
  state.apiRequest = respond([row(1, "Submitted"), row(2, "Submitted", { assigned_coordinator_id: 3, assigned_coordinator: "coord2" })]);
  await state.fetchLeadData();
  assert.equal(state.leadEvents[0].selectedCoordinatorId, "3", "kept");
  assert.equal(state.leadEvents[1].selectedCoordinatorId, 3, "someone else reassigned it: show the new assignment");
});

test("TC-9954 requests needing a Coordinator, then past their proposed date, come first", () => {
  const state = app();
  state.leadEvents = [
    row(1, "Approved", { assigned_coordinator_id: 2 }),
    row(2, "Under Review", { assigned_coordinator_id: 2, proposed_start: "2020-01-01T09:00:00" }),
    row(3, "Submitted"),
    row(4, "Approved", { assigned_coordinator_id: 2, proposed_start: "2020-01-01T09:00:00" }),
  ];
  assert.equal(state.leadView.sort, "attention", "the default");
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [3, 2, 1, 4]);
  assert.equal(state.leadDatePassed(state.leadEvents[3]), false, "a decided request isn't flagged");
  state.leadView.sort = "submitted";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [1, 2, 3, 4], "the backend's order");
});

test("TC-9955 workload counts each Coordinator's open requests from the loaded list", () => {
  const state = app();
  state.leadEvents = [row(1, "Submitted", { assigned_coordinator_id: 2 }), row(2, "Approved", { assigned_coordinator_id: 2 }), row(3, "Rejected", { assigned_coordinator_id: 2 }), row(4, "Under Review", { assigned_coordinator_id: 3 })];
  assert.equal(state.coordinatorOpenCount(2), 2, "Rejected isn't open");
  assert.equal(state.coordinatorOpenCount(3), 1);
  assert.match(html, /x-text="pickerOptionLabel\(coordinator\)"/, "shown in the coordinator picker");
  assert.match(html, /<details class="disclosure lead-workload"[^>]*><summary><span class="disclosure-title">Coordinator workload<\/span>/);
  assert.match(html, /x-for="coordinator in coordinatorsByWorkload\(\)"/);
});

test("TC-9956 the Lead screen has loading, error, permission-denied and empty states", async () => {
  const state = app();
  assert.equal(state.leadView.loaded, false);
  state.apiRequest = async () => ({ ok: false, status: 403, data: { detail: "Lead role required" } });
  await state.fetchLeadData();
  assert.deepEqual([state.leadView.denied, state.leadView.error], [true, "Lead role required"]);
  state.apiRequest = async path => path === "/lead/events" ? { ok: true, status: 200, data: [row(1, "Submitted")] } : { ok: false, status: 0, data: { detail: "Cannot reach the backend service." } };
  await state.fetchLeadData();
  assert.deepEqual([state.leadView.loaded, state.leadView.denied, state.leadView.error], [true, false, ""]);
  assert.equal(state.leadView.coordinatorsError, "Cannot reach the backend service.");
  resetSignedOutState(state);
  assert.equal(state.leadView.loaded, false);
  for (const label of ["Loading requests…", "You can't view assignments", "Couldn't load the requests", "No submitted requests", "assignment is unavailable"]) assert.ok(html.includes(label), label);
});

test("TC-9957 the coordinator picker orders by fewest open requests then name, searches by name, and words counts correctly", () => {
  const state = app();
  state.coordinators = [{ id: 2, username: "zara" }, { id: 3, username: "amir" }, { id: 4, username: "ben", display_name: "Ben Tan" }, { id: 5, username: "cleo" }];
  state.leadEvents = [row(1, "Submitted", { assigned_coordinator_id: 2 }), row(2, "Approved", { assigned_coordinator_id: 2 }), row(3, "Under Review", { assigned_coordinator_id: 5 }), row(4, "Rejected", { assigned_coordinator_id: 3 })];
  const labels = () => plain(state.pickerOptions()).map(option => state.pickerOptionLabel(option));
  assert.deepEqual(labels(), ["amir (0 open requests)", "Ben Tan (0 open requests)", "cleo (1 open request)", "zara (2 open requests)"], "display name used when the backend gives one");
  state.picker.query = "BE";
  assert.deepEqual(labels(), ["Ben Tan (0 open requests)"]);
  state.picker.query = "zz";
  assert.deepEqual(labels(), [], "the no-results state shows");
  assert.match(html, /x-text="`No coordinators match “\$\{picker\.query\.trim\(\)\}”`"/);
  assert.deepEqual(plain(state.coordinatorsByWorkload()).map(coordinator => `${coordinator.name} ${coordinator.open}`), ["amir 0", "Ben Tan 0", "cleo 1", "zara 2"], "the workload table uses the same order");
});

test("TC-9958 choosing in the picker only sets the pending choice; keyboard moves, chooses and closes", () => {
  const state = app();
  const event = row(8, "Under Review", { assigned_coordinator_id: 2, assigned_coordinator: "coord1", selectedCoordinatorId: 2 });
  state.leadEvents = [event];
  state.apiRequest = async () => assert.fail("nothing is saved until Assign or Reassign");
  state.openPicker(event);
  assert.equal(state.pickerOpen(event), true);
  const key = name => { const keyboard = { key: name, preventDefault() {}, stopPropagation() {} }; state.pickerKeydown(event, keyboard); };
  assert.equal(plain(state.pickerOptions())[0].username, "coord2", "coord1 has 1 open request, coord2 none: coord2 first");
  key("ArrowDown");
  assert.equal(state.picker.active, 1);
  key("ArrowDown");
  assert.equal(state.picker.active, 0, "wraps around");
  key("End");
  key("Enter");
  assert.equal(state.pickerOpen(event), false, "choosing closes the picker");
  assert.equal(String(event.selectedCoordinatorId), "2");
  state.openPicker(event);
  key("Home");
  key("Enter");
  assert.equal(String(event.selectedCoordinatorId), "3");
  assert.equal(state.pickerChoiceLabel(event), "coord2");
  assert.equal(state.assignmentChanged(event), true, "Reassign is now enabled");
  state.openPicker(event);
  key("Escape");
  assert.equal(state.pickerOpen(event), false);
  // The current Coordinator is marked in the list
  assert.match(html, /<template x-if="String\(coordinator\.id\) === String\(event\.assigned_coordinator_id\)"><span class="picker-current">Current<\/span><\/template>/);
  assert.match(html, /role="combobox" aria-autocomplete="list"/);
  assert.match(html, /role="listbox" :id="`picker-list-\$\{event\.id\}`" :aria-label="`Coordinators for request #\$\{event\.id\}`"/);
});

// ---- Save actions only after a change, with Cancel (UI_DECISIONS "Inline changes")
function leadPage() {
  const focused = [];
  const state = app({ addEventListener: (_, fn) => fn(), getElementById: id => ({ focus: () => focused.push(id) }), querySelector: () => null });
  state.$nextTick = fn => fn();
  return { state, focused };
}

test("TC-9981 the save actions appear only when the choice differs; Cancel restores the saved value without a call", () => {
  const { state, focused } = leadPage();
  const event = row(4, "Under Review", { assigned_coordinator_id: 2, assigned_coordinator: "coord1", selectedCoordinatorId: 2 });
  state.apiRequest = async () => assert.fail("Cancel sends nothing");
  assert.equal(state.assignmentActionsShown(event), false, "hidden while the saved Coordinator is selected");
  state.openPicker(event);
  state.choosePickerOption(event, { id: 3, username: "coord2" });
  assert.equal(state.assignmentActionsShown(event), true);
  assert.equal(focused.at(-1), "picker-4", "focus returns to the picker; Tab reaches Reassign next");
  state.choosePickerOption(event, { id: 2, username: "coord1" });
  assert.equal(state.assignmentActionsShown(event), false, "choosing the saved Coordinator again hides them");
  state.choosePickerOption(event, { id: 3, username: "coord2" });
  state.cancelAssignment(event);
  assert.equal(event.selectedCoordinatorId, 2);
  assert.equal(state.assignmentActionsShown(event), false);
  assert.equal(focused.at(-1), "picker-4", "Cancel hands focus back to the picker");
  // Unassigned: Cancel goes back to no Coordinator
  const open = row(5, "Submitted", { selectedCoordinatorId: "" });
  state.choosePickerOption(open, { id: 2, username: "coord1" });
  state.cancelAssignment(open);
  assert.equal(open.selectedCoordinatorId, "");
  assert.equal(state.pickerChoiceLabel(open), "Select coordinator");
});

test("TC-9982 assigning and reassigning hide the actions and show the confirmation; nothing is sent before the click", async () => {
  for (const [saved, action, message] of [[null, "assign", "Assigned to coord2."], [2, "reassign", "Reassigned to coord2."]]) {
    const { state, focused } = leadPage();
    const event = row(6, "Submitted", { assigned_coordinator_id: saved, assigned_coordinator: saved ? "coord1" : null, selectedCoordinatorId: saved || "" });
    state.leadEvents = [event];
    const sent = [];
    state.apiRequest = async (path, options = {}) => { if (options.method === "POST") sent.push([path, options.body.coordinator_id]); return { ok: false, status: 503, data: { detail: "refresh failed" } }; };
    state.apiRequest = (inner => async (path, options = {}) => options.method === "POST" ? (await inner(path, options), { ok: true, status: 200, data: {} }) : inner(path, options))(state.apiRequest);
    state.choosePickerOption(event, { id: 3, username: "coord2" });
    assert.deepEqual(sent, [], "choosing doesn't save");
    await state.saveAssignment(event);
    assert.deepEqual(sent, [[`/events/6/${action}`, 3]]);
    assert.equal(state.assignmentActionsShown(event), false, "hidden after saving, even though the refresh failed");
    assert.equal(state.rowAssignment(event).message, message);
    assert.equal(focused.at(-1), "picker-6");
  }
  assert.match(html, /<p class="form-help lead-assign-message lead-assign-saved" role="status" x-text="assignmentActionsShown\(event\) \? '' : \(rowAssignment\(event\)\.message \|\| ''\)"><\/p>/);
});

test("TC-9983 a failed save keeps the change and the actions, shows the error and keeps focus on the save button", async () => {
  const { state, focused } = leadPage();
  const event = row(7, "Under Review", { assigned_coordinator_id: 2, assigned_coordinator: "coord1", selectedCoordinatorId: 2 });
  state.choosePickerOption(event, { id: 3, username: "coord2" });
  state.apiRequest = async () => ({ ok: false, status: 400, data: { detail: "Coordinator is already assigned to this event request" } });
  await state.saveAssignment(event);
  assert.equal(state.rowAssignment(event).error, "Coordinator is already assigned to this event request");
  assert.equal(state.assignmentActionsShown(event), true, "still unsaved: Reassign and Cancel stay");
  assert.equal(event.assigned_coordinator_id, 2, "the saved value is unchanged");
  assert.equal(focused.at(-1), "assign-save-7");
  state.cancelAssignment(event);
  assert.deepEqual(plain(state.rowAssignment(event)), {}, "Cancel clears the error too");
});

test("TC-9984 the actions keep their space while hidden, so the row doesn't move", () => {
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.lead-assign-slot\{display:grid;flex:none;align-items:center;min-width:10\.5rem\}/);
  assert.match(css, /\.lead-assign-slot>\*\{grid-area:1\/1\}/);
  assert.match(css, /\.lead-assign-actions\.is-hidden\{visibility:hidden\}/, "visibility keeps the space and removes them from the tab order");
  assert.match(html, /@keydown\.escape\.stop="assignmentActionsShown\(event\) && cancelAssignment\(event\)"/);
});
