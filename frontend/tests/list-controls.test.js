import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app(role) {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 2, username: "user", role: normalizeRole(role) };
  return state;
}
const KEYS = ["organiser", "assigned", "allEvents", "lead", "bookings", "technical"];

test("TC-9941 every list uses the one list-controls markup; only its key differs", () => {
  // Each mount runs from its x-data to the end of its no-match state
  const end = "Clear search</button></div></div>";
  const mounts = [...html.matchAll(/<div class="list-controls(?: list-controls-inset)?" x-data="\{ key: '(\w+)' \}">/g)]
    .map(match => [match[1], html.slice(match.index + match[0].length, html.indexOf(end, match.index) + end.length)]);
  assert.deepEqual(mounts.map(([key]) => key).sort(), [...KEYS].sort());
  const bodies = new Set(mounts.map(([, body]) => body));
  assert.equal(bodies.size, 1, "the markup is identical in every mount");
  const body = mounts[0][1];
  assert.match(body, /<label :for="`\$\{key\}-search`">Search<\/label><input type="search" :id="`\$\{key\}-search`"/);
  assert.match(body, /role="status" x-text="listControls\(key\)\.announce"/);
  assert.match(body, /Clear search<\/button>/);
});

test("TC-9942 'All' comes first and is the default; chip counts are what each filter contains, before search", () => {
  const state = app("Coordinator");
  state.venueWorkspace = { bookingFilter: "All", bookings: [{ id: 1, status: "Pending" }, { id: 2, status: "Expired" }] };
  state.leadEvents = [{ id: 1, title: "A", assigned_coordinator_id: null }, { id: 2, title: "B", assigned_coordinator_id: 3, assigned_coordinator: "coord1" }];
  state.assignedEvents = [{ id: 1, title: "A", status: "Submitted" }];
  state.allCoordinatorEvents = [{ id: 1, title: "A", status: "Approved" }, { id: 2, title: "B", status: "Submitted" }];
  state.events = [{ id: 1, title: "A", status: "Draft" }];
  for (const key of KEYS) {
    const controls = state.listControls(key);
    if (!controls.chips) continue;
    assert.ok(["all", "All"].includes(controls.chips[0].key), `${key}: All first`);
    assert.equal(controls.chips[0].label, "All", key);
    assert.equal(controls.filter.target[controls.filter.key], controls.chips[0].key, `${key}: All is selected by default`);
    const before = plain(controls.chips);
    controls.view.query = "zzz";
    assert.deepEqual(plain(state.listControls(key).chips), before, `${key}: counts don't change with search`);
    assert.equal(state.listControls(key).shown, 0, `${key}: search applies to the results`);
    controls.view.query = "";
  }
  assert.deepEqual(plain(state.allEventStatusChips()).map(chip => chip.key), ["all", "Submitted", "Approved"], "statuses follow the workflow, not the data order");
  assert.equal(state.listControls("technical").chips, null, "Technical Support has one kind of record, so no chips");
});

test("TC-9943 placeholders name only what search covers, and the no-match state says what happened", () => {
  const state = app("Venue Staff");
  state.venueWorkspace = { bookingFilter: "Pending", bookings: [{ id: 7, status: "Pending", event_title: "Gala", venue_name: "Harbour Ballroom", booking_start: "2027-01-02T01:00:00" }, { id: 8, status: "Confirmed", event_title: "Fair", venue_name: "Hall", booking_start: "2027-01-01T01:00:00" }] };
  assert.equal(state.listControls("bookings").placeholder, "Event, venue or booking #ID");
  for (const [query, ids] of [["gala", [7]], ["harbour", [7]], ["#7", [7]], ["fair", []]]) {
    state.bookingView.query = query;
    assert.deepEqual(plain(state.filteredVenueBookings()).map(booking => booking.id), ids, query);
  }
  assert.equal(state.listControls("bookings").noMatch, "No bookings match “fair” in Pending");
  assert.equal(state.listControls("bookings").announce, "0 bookings match in Pending");
  state.clearListSearch("bookings");
  assert.equal(state.bookingView.query, "");
  state.venueWorkspace.bookingFilter = "All";
  assert.deepEqual(plain(state.filteredVenueBookings()).map(booking => booking.id), [8, 7], "soonest booking first");
  const placeholders = Object.fromEntries(KEYS.map(key => [key, app("Organiser").listControls(key)?.placeholder]));
  assert.deepEqual(placeholders, {
    organiser: "Title, purpose, description or #ID", assigned: "Title, organiser or #ID", allEvents: "Title, organiser, coordinator or #ID",
    lead: "Title, organiser, coordinator or #ID", bookings: undefined, technical: "Event, equipment, requirements, recorded by or #ID",
  });
});

test("TC-9944 Lead: All, Needs assignment, Assigned; sorted by oldest submission or proposed date", () => {
  const state = app("Event Coordinator Lead");
  state.leadEvents = [
    { id: 1, title: "Gala", organiser: "org1", assigned_coordinator_id: null, proposed_start: "2027-06-01T09:00:00" },
    { id: 2, title: "Fair", organiser: "org2", assigned_coordinator_id: 4, assigned_coordinator: "coord2", proposed_start: "2027-01-01T09:00:00" },
    { id: 3, title: "Talk", organiser: "org1", assigned_coordinator_id: null, proposed_start: null },
  ];
  assert.deepEqual(plain(state.leadFilterChips()).map(chip => `${chip.label} ${chip.count}`), ["All 3", "Needs assignment 2", "Assigned 1"]);
  state.leadView.filter = "unassigned";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [1, 3]);
  state.leadView.sort = "start";
  state.leadView.filter = "all";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [2, 1, 3], "undated requests last");
  state.leadView.query = "coord2";
  assert.deepEqual(plain(state.filteredLeadEvents()).map(event => event.id), [2], "the coordinator is searched");
});

test("TC-9945 Technical Support: search keeps the event grouping; sort orders events and their lines", () => {
  const state = app("Technical Support");
  state.technicalWorklist = { items: [
    { id: 1, event_id: 10, event_title: "Gala", event_proposed_start: "2027-01-01T09:00:00", equipment_type: "Projector", technical_requirements: "4K", requested_by: "coord1", created_at: "2026-10-01T00:00:00" },
    { id: 2, event_id: 10, event_title: "Gala", event_proposed_start: "2027-01-01T09:00:00", equipment_type: "Microphone", technical_requirements: "", requested_by: "coord1", created_at: "2026-10-05T00:00:00" },
    { id: 3, event_id: 11, event_title: "Fair", event_proposed_start: "2027-02-01T09:00:00", equipment_type: "Speaker", technical_requirements: "Outdoor", requested_by: "lead1", created_at: "2026-10-09T00:00:00" },
  ] };
  const shape = () => plain(state.technicalEventGroups()).map(group => [group.event_id, group.items.map(item => item.id)]);
  assert.deepEqual(shape(), [[10, [1, 2]], [11, [3]]], "the backend's soonest-event order");
  state.technicalView.sort = "recorded";
  assert.deepEqual(shape(), [[11, [3]], [10, [2, 1]]]);
  state.technicalView.query = "micro";
  assert.deepEqual(shape(), [[10, [2]]]);
  assert.equal(state.listControls("technical").shown, 1);
  state.technicalView.query = "outdoor";
  assert.deepEqual(shape(), [[11, [3]]], "technical requirements are searched");
});
