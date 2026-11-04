import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app() {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
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
  assert.match(rowHtml, /:disabled="!assignmentChanged\(event\) \|\| !!rowAssignment\(event\)\.busy"/);
  assert.match(rowHtml, /:class="assignmentChanged\(event\) \? 'button-primary' : 'button-secondary'"/, "filled only when there is something to save");
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
