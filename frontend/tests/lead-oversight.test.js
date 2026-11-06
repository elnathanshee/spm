import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

// [SCRUM-85] The Lead reassigns later events, filters by status and sees each Coordinator's assignments.
const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const leadPage = html.slice(html.indexOf("currentPath === '/lead/events'"), html.indexOf("currentPath === '/coordinator/all-events'"));

const ACTIVE = ["Submitted", "Under Review", "Returned", "Approved", "Planning", "Safety Review", "Confirmed"];

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
const row = (id, status, extra = {}) => ({ id, title: `Event ${id}`, organiser: "org1", proposed_start: "2099-01-01T09:00:00", expected_attendance: 20, status, assigned_coordinator_id: 2, assigned_coordinator: "coord1", assigned_coordinator_active: true, submitted_at: "2026-10-01T00:00:00", ...extra });
const unassigned = { assigned_coordinator_id: null, assigned_coordinator: null, assigned_coordinator_active: null };
const left = { assigned_coordinator_id: 7, assigned_coordinator: "coord_left", assigned_coordinator_active: false };

test("TC-8520 an assigned event offers Reassign until it is finished", () => {
  const state = app();
  for (const status of ACTIVE) assert.equal(state.canAssignEvent(row(1, status)), true, status);
  for (const status of ["Draft", "Rejected", "Completed", "Cancelled"]) assert.equal(state.canAssignEvent(row(1, status)), false, status);
});

test("TC-8521 an unassigned event can only be first assigned while in review", () => {
  const state = app();
  for (const status of ["Submitted", "Under Review", "Returned"]) assert.equal(state.canAssignEvent(row(1, status, unassigned)), true, status);
  for (const status of ["Approved", "Planning", "Safety Review", "Confirmed", "Rejected"]) assert.equal(state.canAssignEvent(row(1, status, unassigned)), false, status);
});

test("TC-8522 saving on a Confirmed event sends a reassignment and shows the confirmation", async () => {
  const state = app();
  const event = row(8, "Confirmed", { selectedCoordinatorId: 2 });
  state.leadEvents = [event];
  const sent = [];
  state.fetchLeadData = async () => {};
  state.apiRequest = async (path, options) => { sent.push([path, options.body.coordinator_id]); return { ok: true, status: 200, data: {} }; };
  state.choosePickerOption(event, { id: 3, username: "coord2" });
  assert.equal(state.assignmentActionsShown(event), true);
  await state.saveAssignment(event);
  assert.deepEqual(sent, [["/events/8/reassign", 3]]);
  assert.equal(state.rowAssignment(event).message, "Reassigned to coord2.");
  assert.equal(event.status, "Confirmed", "the status doesn't move");
});

test("TC-8523 status chips follow the workflow order, with counts, and filter the list", () => {
  const state = app();
  state.leadEvents = [row(1, "Confirmed"), row(2, "Approved"), row(3, "Approved", unassigned), row(4, "Safety Review"), row(5, "Cancelled")];
  assert.deepEqual(plain(state.leadStatusChips()).map(chip => `${chip.label} ${chip.count}`), ["All statuses 5", "Approved 2", "Safety Review 1", "Confirmed 1", "Cancelled 1"]);
  assert.equal(state.leadView.status, "all", "the default shows everything");
  state.leadView.status = "Approved";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [2, 3]);
  state.leadView.status = "Safety Review";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [4]);
});

test("TC-8524 the status and assignment filters narrow each other, and their counts agree", () => {
  const state = app();
  state.leadEvents = [row(1, "Approved"), row(2, "Approved", unassigned), row(3, "Planning"), row(4, "Submitted", unassigned)];
  state.leadView.status = "Approved";
  assert.deepEqual(plain(state.leadFilterChips()).map(chip => `${chip.label} ${chip.count}`), ["All 2", "Needs assignment 1", "Assigned 1"]);
  state.leadView.filter = "assigned";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [1]);
  state.leadView.status = "all";
  assert.deepEqual(plain(state.leadStatusChips()).map(chip => `${chip.label} ${chip.count}`), ["All statuses 2", "Submitted 0", "Approved 1", "Planning 1"]);
});

test("TC-8525 a chosen status stays selectable when a refresh empties it, and the empty message names it", () => {
  const state = app();
  state.leadEvents = [row(1, "Planning")];
  state.leadView.status = "Approved";
  assert.deepEqual(plain(state.leadStatusChips()).map(chip => `${chip.label} ${chip.count}`), ["All statuses 1", "Approved 0", "Planning 1"]);
  assert.deepEqual(plain(state.filteredLeadEvents()), []);
  assert.equal(state.leadEmptyMessage(), "No Approved requests in this view");
  state.leadView.status = "all";
  state.leadEvents = [row(1, "Planning", unassigned)];
  state.leadView.filter = "assigned";
  assert.equal(state.leadEmptyMessage(), "No requests are assigned yet");
  state.leadEvents = [row(1, "Planning")];
  state.leadView.filter = "unassigned";
  assert.equal(state.leadEmptyMessage(), "Every request has a Coordinator");
});

test("TC-8526 assignments are grouped by Coordinator: only active events, nobody left out", () => {
  const state = app();
  state.coordinators = [{ id: 2, username: "zara" }, { id: 3, username: "amir" }, { id: 4, username: "ben", display_name: "Ben Tan" }];
  state.leadEvents = [
    row(1, "Confirmed", { assigned_coordinator: "zara" }),
    row(2, "Safety Review", { assigned_coordinator: "zara" }),
    row(3, "Completed", { assigned_coordinator: "zara" }),
    row(4, "Approved", left),
    row(5, "Submitted", unassigned),
    row(6, "Rejected", { assigned_coordinator_id: 3, assigned_coordinator: "amir" }),
  ];
  const groups = plain(state.coordinatorAssignments()).map(group => [group.name, group.inactive, group.events.map(event => event.id)]);
  assert.deepEqual(groups, [
    ["amir", false, []],
    ["Ben Tan", false, []],
    ["coord_left", true, [4]],
    ["zara", false, [1, 2]],
    ["Not assigned", false, [5]],
  ]);
});

test("TC-8527 when coordinators fail to load, holders are still listed and aren't called inactive", () => {
  const state = app();
  state.coordinators = [];
  state.leadEvents = [row(1, "Approved")];
  assert.deepEqual(plain(state.coordinatorAssignments()).map(group => [group.name, group.inactive]), [["coord1", false]]);
});

test("TC-8528 a row held by a Coordinator who has left names them and can move to someone active", () => {
  const state = app();
  const event = row(4, "Approved", { ...left, selectedCoordinatorId: 7 });
  assert.equal(state.pickerChoiceLabel(event), "coord_left (no longer active)");
  assert.equal(state.assignmentChanged(event), false);
  state.choosePickerOption(event, { id: 3, username: "coord2" });
  assert.equal(state.pickerChoiceLabel(event), "coord2");
  assert.equal(state.assignmentChanged(event), true);
  assert.equal(state.pickerChoiceLabel(row(5, "Submitted", { ...unassigned, selectedCoordinatorId: "" })), "Select coordinator");
});

test("TC-8529 the Lead screen has the status chips and the assignments list, in the words the Lead reads", () => {
  assert.match(leadPage, /<div class="filter-chips lead-status-chips" role="group" aria-label="Show requests by status"><template x-for="chip in leadStatusChips\(\)"/);
  assert.match(leadPage, /@click="leadView\.status = chip\.key"/);
  assert.match(leadPage, /:aria-pressed="leadView\.status === chip\.key"/);
  assert.match(leadPage, /x-text="leadEmptyMessage\(\)"/);
  assert.match(leadPage, /<details class="disclosure lead-assignments"[^>]*><summary><span class="disclosure-title">Assignments by Coordinator<\/span>/);
  assert.match(leadPage, /x-for="holder in coordinatorAssignments\(\)"/);
  assert.match(leadPage, /x-for="item in holder\.events"/);
  assert.match(leadPage, /@click\.prevent="openEventDetail\(item\.id\)"/);
  assert.match(leadPage, /x-show="holder\.inactive">No longer active</);
  assert.match(leadPage, /reassign until the event is finished/);
  assert.doesNotMatch(leadPage, /You can assign while a request is Submitted, Under Review or Returned\.</, "the old limit is gone from the page");
  assert.match(css, /\.lead-assignments\{/);
});

test("TC-8530 signing out clears the status filter", () => {
  const state = { leadView: { status: "Approved" } };
  resetSignedOutState(state);
  assert.equal(state.leadView.status, "all");
  assert.equal(app().leadView.status, "all");
});
