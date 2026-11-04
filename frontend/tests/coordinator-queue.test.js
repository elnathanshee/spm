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
  state.user = { user_id: 2, username: "coord1", role: "Coordinator" };
  return state;
}
// Rows as GET /coordinator/events/data returns them (assigned to this Coordinator)
const row = (id, status, extra = {}) => ({ id, title: `Event ${id}`, organiser: "organiser1", proposed_start: "2027-01-01T09:00:00", expected_attendance: 50, status, awaiting_first_review: status === "Submitted", ...extra });
const statuses = [...readFileSync(new URL("../../backend/status.py", import.meta.url), "utf8").matchAll(/=\s*"([^"]+)"/g)].map(match => match[1]).filter(status => status !== "Draft");

test("TC-9931 active work and completed requests are separate, one section per status, in the backend's order", () => {
  const state = app();
  state.assignedEvents = [row(1, "Approved"), row(2, "Submitted"), row(3, "Rejected"), row(4, "Under Review"), row(5, "Returned"), row(6, "Submitted"), row(7, "Mystery")];
  let groups = plain(state.coordinatorGroups());
  assert.deepEqual(groups.map(group => group.label), ["Awaiting first review", "Under review", "Waiting on the Organiser", "Approved", "Other statuses"]);
  assert.deepEqual(groups[0].events.map(event => event.id), [2, 6], "the backend's longest-wait order is kept");
  state.assignedView.scope = "completed";
  groups = plain(state.coordinatorGroups());
  assert.deepEqual(groups.map(group => group.label), ["Rejected"]);
  assert.deepEqual(plain(state.coordinatorScopeChips()), [{ key: "active", label: "Active", count: 6 }, { key: "completed", label: "Completed", count: 1 }]);
  // Every non-Draft status has a named section, so none falls into "Other"
  for (const status of statuses) assert.notEqual(state.coordinatorGroupOf({ status }).key, "other", status);
});

test("TC-9932 counters add up the sections shown, so they always match the queue, including while searching", () => {
  const state = app();
  state.assignedEvents = [row(1, "Submitted", { title: "Gala" }), row(2, "Submitted", { title: "Fair" }), row(3, "Under Review", { title: "Gala lunch" }), row(4, "Approved"), row(5, "Planning"), row(6, "Rejected")];
  const counters = () => plain(state.coordinatorCounters()).map(counter => [counter.label, counter.count, counter.sections.join(" + ")]);
  assert.deepEqual(counters(), [["Need a decision", 3, "Awaiting first review + Under review"], ["Approved and in planning", 2, "Approved + Planning"]]);
  // The counters equal the rows in the sections they name
  const shown = plain(state.coordinatorGroups()).reduce((sum, group) => sum + group.events.length, 0);
  assert.equal(plain(state.coordinatorCounters()).reduce((sum, counter) => sum + counter.count, 0), shown);
  state.assignedView.query = "gala";
  assert.deepEqual(counters(), [["Need a decision", 2, "Awaiting first review + Under review"]]);
  state.assignedView.query = "ORGANISER1";
  assert.equal(counters().length, 2, "the organiser name is searched too");
  state.assignedView.query = "";
  state.assignedView.scope = "completed";
  assert.deepEqual(counters(), [], "completed requests are counted on their chip, not the active counters");
});

test("TC-9933 each row has at most one main action, matching what the backend allows", () => {
  const state = app();
  state.assignedEvents = statuses.map((status, index) => row(index + 1, status));
  const labels = Object.fromEntries(state.assignedEvents.map(event => [event.status, state.coordinatorPrimary(event)?.label ?? null]));
  assert.deepEqual(labels, {
    Submitted: "Start review", "Under Review": "Record decision", Approved: null, Rejected: null, Returned: null,
    Planning: null, Confirmed: null, Completed: null, Cancelled: null,
  });
  // A request not assigned to this Coordinator gets no decision action
  assert.equal(state.coordinatorPrimary({ ...row(99, "Submitted"), assigned_coordinator_id: 7 }), null);
});

test("TC-9934 Venue bookings stays available in every status, as the backend allows (BACKEND_ISSUES #5)", () => {
  const state = app();
  state.assignedEvents = statuses.map((status, index) => row(index + 1, status));
  for (const event of state.assignedEvents) assert.equal(state.canBookEvent(event), true, event.status);
  const start = html.indexOf('x-for="event in group.events"', html.indexOf("currentPath === '/coordinator/events'"));
  const rowHtml = html.slice(start, html.indexOf("</li></template>", start));
  assert.match(rowHtml, /<template x-if="canBookEvent\(event\)"><button type="button" class="button button-secondary" @click="openBookings\(event\)">/);
});

test("TC-9935 both Coordinator lists show loading, error and permission-denied states without dropping loaded rows", async () => {
  const state = app();
  assert.equal(state.assignedView.loaded, false);
  state.apiRequest = async () => ({ ok: false, status: 403, data: { detail: "Coordinator role required" } });
  await state.fetchAssignedEvents();
  assert.deepEqual([state.assignedView.denied, state.assignedView.error], [true, "Coordinator role required"]);
  state.apiRequest = async () => ({ ok: true, status: 200, data: [row(1, "Submitted")] });
  await state.fetchAssignedEvents();
  assert.deepEqual([state.assignedView.loaded, state.assignedView.denied, state.assignedView.error], [true, false, ""]);
  state.apiRequest = async () => ({ ok: false, status: 0, data: { detail: "Cannot reach the backend service." } });
  await state.fetchAssignedEvents();
  assert.equal(state.assignedEvents.length, 1);
  assert.equal(state.assignedView.error, "Cannot reach the backend service.");
  assert.ok(!state.alert?.message, "shown on the page, not as a second alert");
  await state.fetchAllCoordinatorEvents();
  assert.equal(state.allEventsView.error, "Cannot reach the backend service.");
  resetSignedOutState(state);
  assert.equal(state.assignedView.loaded, false);
  assert.equal(state.allEventsView.loaded, false);
  for (const label of ["Loading your requests…", "You can't view assigned requests", "No requests assigned to you", "Loading events…", "You can't view these events", "No events match"]) assert.ok(html.includes(label), label);
});

test("TC-9936 All events can be searched and filtered by status from the loaded list", () => {
  const state = app();
  state.allCoordinatorEvents = [row(1, "Submitted", { assigned_coordinator: null }), row(2, "Approved", { assigned_coordinator: "coord2", title: "Harbour Gala" }), row(3, "Approved", { assigned_coordinator: "coord1" })];
  assert.deepEqual(plain(state.allEventStatusChips()), [{ key: "all", label: "All", count: 3 }, { key: "Submitted", label: "Submitted", count: 1 }, { key: "Approved", label: "Approved", count: 2 }]);
  state.allEventsView.status = "Approved";
  assert.deepEqual(plain(state.filteredAllEvents()).map(event => event.id), [2, 3]);
  state.allEventsView.query = "coord2";
  assert.deepEqual(plain(state.filteredAllEvents()).map(event => event.id), [2]);
});
