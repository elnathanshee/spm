import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";
import * as venueRecord from "../src/venue-record.js";

function app(role = "venue_staff") {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], requestedFrontendPath: () => null, normalizeRoleName: normalizeRole,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} }, ...venueRecord,
  });
  const state = factory(); state.user = { user_id: 3, role }; return state;
}

test("TC-3011 Venue Staff have a functional route; other roles cannot use staff actions", async () => {
  assert.equal(resolveRolePath("/venue", "venue_staff").unbuiltRole, false);
  for (const role of ["lead", "coordinator", "organiser", "attendee", "technical_support"]) {
    assert.equal(resolveRolePath("/venue", role).accessDenied, true);
    const state = app(role); state.apiRequest = () => assert.fail("unauthorized request");
    await state.fetchVenueWorkspace(); await state.createVenue();
    await state.openStaffBooking(1); await state.decideStaffBooking("approve");
    assert.equal(state.staffBooking, null);
  }
});

test("TC-3012 empty catalogue, validation, venue creation and refreshed listing", async () => {
  const state = app();
  let created = false;
  state.apiRequest = async (path, options) => {
    if (options?.method === "POST") {
      assert.equal(path, "/venues"); assert.equal(options.body.capacity, 100);
      assert.equal(options.body.name, "Hall"); created = true; return { ok: true };
    }
    return { ok: true, data: path === "/venues" && created ? [{ id: 1, name: "Hall" }] : [] };
  };
  await state.fetchVenueWorkspace();
  assert.equal(state.venueWorkspace.loaded, true); assert.equal(state.venueWorkspace.venues.length, 0);
  assert.equal(state.venueWorkspace.bookingFilter, "All");
  await state.createVenue(); assert.ok(state.fieldErrors.name); assert.equal(created, false);
  Object.assign(state.venueWorkspace.form, { name: " Hall ", location: "Campus", capacity: "100" });
  await state.createVenue(); assert.equal(state.alert.kind, "success");
  assert.equal(state.venueWorkspace.venues[0].name, "Hall");
  assert.equal(state.venueWorkspace.form.name, "");
});

test("TC-3614 review uses existing decisions, validates reason, blocks repeated clicks and decisions", async () => {
  const state = app();
  state.apiRequest = async path => ({ ok: true, data: { id: 9, status: "Pending" } });
  await state.openStaffBooking(9);
  await state.decideStaffBooking("reject"); assert.ok(state.fieldErrors.reason);
  state.staffBooking.reason = "  Not available  ";
  let complete;
  state.apiRequest = (path, options) => {
    assert.equal(path, "/venues/bookings/9/reject"); assert.equal(options.body.reason, "Not available");
    return new Promise(resolve => { complete = resolve; });
  };
  state.fetchVenueWorkspace = async () => {};
  const pending = state.decideStaffBooking("reject");
  assert.equal(state.staffBooking.busy, true); await state.decideStaffBooking("reject");
  complete({ ok: true, data: { booking: { id: 9, status: "Rejected" } } }); await pending;
  assert.equal(state.staffBooking.detail.status, "Rejected"); assert.equal(state.staffBooking.busy, false);
  state.apiRequest = () => assert.fail("already decided"); await state.decideStaffBooking("approve");
});

test("TC-3615 409 refreshes current decision; catalogue errors do not leave loading stuck", async () => {
  const state = app();
  state.apiRequest = async () => ({ ok: false, status: 500 });
  await state.fetchVenueWorkspace(); assert.equal(state.venueWorkspace.busy, false);
  assert.equal(state.venueWorkspace.loaded, false);
  state.staffBooking = { id: 9, detail: { status: "Pending" }, reason: "reason", busy: false };
  state.apiRequest = async (_, options) => options?.method ? { ok: false, status: 409 } : { ok: true, data: { status: "Confirmed" } };
  await state.decideStaffBooking("reject"); assert.equal(state.staffBooking.detail.status, "Confirmed");
});

test("TC-6427 mark read reuses API; logout clears staff state and discards late results", async () => {
  const state = app();
  state.notifications = [{ id: 4, read: false }];
  state.apiRequest = async (path, options) => {
    assert.equal(path, "/notifications/4/read"); assert.equal(options.method, "POST");
    return { ok: true, data: { id: 4, read: true } };
  };
  await state.markNotificationRead(state.notifications[0]); assert.equal(state.notifications[0].read, true);
  let complete;
  state.apiRequest = () => new Promise(resolve => { complete = resolve; });
  const pending = state.fetchVenueWorkspace(); resetSignedOutState(state);
  complete({ ok: true, data: [{ id: 1 }] }); await pending;
  assert.equal(state.venueWorkspace, null); assert.equal(state.staffBooking, null);
});

test("TC-6428 staff notification rendering uses safe text, unread state, read action and booking link", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /is-unread/); assert.match(html, /markNotificationRead\(notification\)/);
  assert.match(html, /openNotification\(notification\)/);
  assert.match(html, /x-text="formatNotificationMessage\(notification\)"/);
  assert.match(html, /No venues yet/); assert.match(html, /No booking requests/);
});

test("TC-3616 booking filters use the existing workspace data without extra requests", () => {
  const state = app();
  state.venueWorkspace = { bookingFilter: "Pending", bookings: [
    { id: 1, status: "Pending" }, { id: 2, status: "Confirmed" }, { id: 3, status: "Rejected" },
  ] };
  assert.deepEqual(Array.from(state.filteredVenueBookings(), booking => booking.id), [1]);
  assert.equal(state.bookingFilterCount("All"), 3);
  assert.equal(state.bookingFilterCount("Confirmed"), 1);
  state.venueWorkspace.bookingFilter = "All";
  assert.equal(state.filteredVenueBookings().length, 3);
});

// ---------------------------------------------------------------------------
// SCRUM-92: the full venue record on the Venue Staff screen. Expected values
// come from SCRUM-92's criteria and the PO's decisions D2 to D8.
// ---------------------------------------------------------------------------

// Objects made inside the vm come from another realm; compare plain copies.
const plain = value => JSON.parse(JSON.stringify(value));

const grandHall = () => ({
  id: 7, name: "Grand Hall", location: "Block A", capacity: 120,
  facilities: ["Projector", "Stage"], accessibility: "Ramp", supported_layouts: ["Theatre"],
  operating_hours: "08:00-22:00", setup_time_mins: 30, turnaround_time_mins: 45, is_active: true,
});

// Records every request; answers from the given routes ("METHOD /path": response).
function serve(state, routes) {
  const calls = [];
  state.apiRequest = async (path, options = {}) => {
    const method = options.method || "GET";
    calls.push({ method, path, body: options.body });
    const answer = routes[`${method} ${path}`];
    if (typeof answer === "function") return answer(options);
    return answer || { ok: true, data: method === "GET" && path === "/venues" ? [] : {} };
  };
  return calls;
}

async function staffWithCatalogue(venues = [grandHall()]) {
  const state = app();
  serve(state, { "GET /venues": { ok: true, data: venues } });
  await state.fetchVenueWorkspace();
  return state;
}

const html = () => readFileSync(new URL("../index.html", import.meta.url), "utf8");

test("TC-9201 Add venue with every field sends one POST with all nine fields, and the venue appears with its attributes", async () => {
  const state = await staffWithCatalogue([]);
  const created = { ...grandHall(), id: 8, name: "Harbour Ballroom" };
  const calls = serve(state, {
    "POST /venues": { ok: true, status: 201, data: { venue: created } },
    "GET /venues": { ok: true, data: [created] },
  });
  state.toggleVenueCreate();
  Object.assign(state.venueWorkspace.form, {
    name: "Harbour Ballroom", location: "Block B, Level 2", capacity: "250",
    facilities: "Projector, Stage, WiFi", accessibility: "Step-free entrance; hearing loop",
    supported_layouts: "Theatre, Banquet", operating_hours: "Mon-Fri 08:00-22:00",
    setup_time_mins: "20", turnaround_time_mins: "60",
  });
  await state.saveVenueForm();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, "/venues");
  assert.deepEqual(plain(posts[0].body), {
    name: "Harbour Ballroom", location: "Block B, Level 2", capacity: 250,
    facilities: ["Projector", "Stage", "WiFi"], accessibility: "Step-free entrance; hearing loop",
    supported_layouts: ["Theatre", "Banquet"], operating_hours: "Mon-Fri 08:00-22:00",
    setup_time_mins: 20, turnaround_time_mins: 60,
  });
  assert.equal(state.venueWorkspace.showCreate, false);
  assert.deepEqual(plain(state.venueWorkspace.venues), [plain(created)]);
});

test("TC-9202 a new venue form shows 30 and 45 for setup and turnaround", async () => {
  const state = await staffWithCatalogue([]);
  state.toggleVenueCreate();
  assert.equal(state.venueWorkspace.showCreate, true);
  assert.equal(state.venueWorkspace.form.setup_time_mins, "30");
  assert.equal(state.venueWorkspace.form.turnaround_time_mins, "45");
});

test("TC-9203 invalid Add venue input is caught in the form, and nothing is sent", async () => {
  const state = await staffWithCatalogue([]);
  const calls = serve(state, {});
  state.toggleVenueCreate();
  Object.assign(state.venueWorkspace.form, { name: "  ", location: "", capacity: "0", setup_time_mins: "-5" });
  await state.saveVenueForm();
  assert.deepEqual(calls, []);
  assert.deepEqual(Object.keys(state.fieldErrors).sort(), ["capacity", "location", "name", "setup_time_mins"]);
  assert.equal(state.venueWorkspace.showCreate, true);
});

test("TC-9204 capacity 1 and setup 0 are sent; capacity 0 and setup -1 are not", async () => {
  const state = await staffWithCatalogue([]);
  const calls = serve(state, { "POST /venues": { ok: true, status: 201, data: {} } });
  state.toggleVenueCreate();
  Object.assign(state.venueWorkspace.form, { name: "Hall", location: "Campus", capacity: "0", setup_time_mins: "-1" });
  await state.saveVenueForm();
  assert.equal(calls.length, 0);
  assert.ok(state.fieldErrors.capacity); assert.ok(state.fieldErrors.setup_time_mins);
  Object.assign(state.venueWorkspace.form, { capacity: "1", setup_time_mins: "0" });
  await state.saveVenueForm();
  const post = calls.find(call => call.method === "POST");
  assert.equal(post.body.capacity, 1);
  assert.equal(post.body.setup_time_mins, 0);
});

test("TC-9205 Edit loads GET /venues/{id}, prefills every field, and changing only capacity sends only capacity", async () => {
  const state = await staffWithCatalogue();
  const calls = serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": { ok: true, data: { venue: { ...grandHall(), capacity: 150 }, updated_fields: ["capacity"], flagged_bookings: [] } },
  });
  await state.openVenueEdit(grandHall());
  assert.equal(calls[0].path, "/venues/7");
  assert.equal(state.venueWorkspace.showCreate, true);
  assert.equal(state.venueWorkspace.editing.id, 7);
  assert.deepEqual(plain(state.venueWorkspace.form), {
    name: "Grand Hall", location: "Block A", capacity: "120", facilities: "Projector, Stage",
    accessibility: "Ramp", supported_layouts: "Theatre", operating_hours: "08:00-22:00",
    setup_time_mins: "30", turnaround_time_mins: "45",
  });
  state.venueWorkspace.form.capacity = "150";
  await state.saveVenueForm();
  const patch = calls.find(call => call.method === "PATCH");
  assert.equal(patch.path, "/venues/7");
  assert.deepEqual(plain(patch.body), { capacity: 150 });
  assert.equal(state.venueWorkspace.showCreate, false);
  assert.equal(state.venueWorkspace.editing, null);
  assert.equal(state.alert.kind, "success");
});

test("TC-9206 saving an edit with no changes sends nothing and says so", async () => {
  const state = await staffWithCatalogue();
  const calls = serve(state, { "GET /venues/7": { ok: true, data: grandHall() } });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.name = " Grand Hall ";
  await state.saveVenueForm();
  assert.equal(calls.filter(call => call.method !== "GET").length, 0);
  assert.deepEqual(plain(state.venueWorkspace.formMessage), { kind: "info", text: "No changes to save." });
  assert.equal(state.venueWorkspace.showCreate, true);
});

test("TC-9207 a 409 and a 422 are shown inside the form, which stays open with the user's input", async () => {
  const state = await staffWithCatalogue();
  const duplicate = "A venue named 'Small Room' already exists";
  serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    // apiRequest puts a 409 in the page alert; the form takes it over.
    "PATCH /venues/7": () => { state.alert = { message: duplicate, kind: "error" }; return { ok: false, status: 409, data: { detail: duplicate } }; },
  });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.name = "Small Room";
  await state.saveVenueForm();
  assert.deepEqual(plain(state.venueWorkspace.formMessage), { kind: "error", text: duplicate });
  assert.equal(state.alert.message, "");
  assert.equal(state.venueWorkspace.showCreate, true);
  assert.equal(state.venueWorkspace.form.name, "Small Room");

  serve(state, { "PATCH /venues/7": { ok: false, status: 422, data: { detail: [
    { type: "value_error", loc: ["body", "capacity"], msg: "Value error, Capacity must be greater than 0" },
  ] } } });
  state.venueWorkspace.form.capacity = "99";
  await state.saveVenueForm();
  assert.equal(state.fieldErrors.capacity, "Capacity must be greater than 0");
  assert.equal(state.venueWorkspace.showCreate, true);
  assert.equal(state.venueWorkspace.form.capacity, "99");

  // The same on Add venue.
  state.closeVenueForm(); state.toggleVenueCreate();
  serve(state, { "POST /venues": { ok: false, status: 409, data: { detail: "A venue named 'Grand Hall' already exists" } } });
  Object.assign(state.venueWorkspace.form, { name: "Grand Hall", location: "Block A", capacity: "50" });
  await state.saveVenueForm();
  assert.equal(state.venueWorkspace.formMessage.text, "A venue named 'Grand Hall' already exists");
  assert.equal(state.venueWorkspace.form.name, "Grand Hall");
});

test("TC-9208 after a buffer change, the flagged bookings are listed; an empty list says none were newly flagged", async () => {
  const state = await staffWithCatalogue();
  const flagged = (id, title, start, end, other) => ({
    booking_id: id, id, venue_id: 7, event_title: title, booking_start: start, booking_end: end, status: "Confirmed",
    conflict_flag: { conflicting_booking_id: other, reason: `Clashes with booking #${other}`, flagged_at: "2026-10-26T09:00:00" },
  });
  serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": { ok: true, data: { venue: { ...grandHall(), turnaround_time_mins: 90 }, updated_fields: ["turnaround_time_mins"], flagged_bookings: [
      flagged(1, "Annual Gala", "2026-11-16T10:00:00", "2026-11-16T12:00:00", 2),
      flagged(2, "Spring Fair", "2026-11-16T13:30:00", "2026-11-16T15:00:00", 1),
    ] } },
  });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.turnaround_time_mins = "90";
  await state.saveVenueForm();
  const outcome = plain(state.venueWorkspace.outcome);
  assert.equal(outcome.kind, "updated");
  assert.equal(outcome.bufferChanged, true);
  assert.deepEqual(outcome.flagged.map(row => [row.venueName, row.eventTitle, row.start, row.end, row.reason]), [
    ["Grand Hall", "Annual Gala", "2026-11-16T10:00:00", "2026-11-16T12:00:00", "Clashes with booking #2"],
    ["Grand Hall", "Spring Fair", "2026-11-16T13:30:00", "2026-11-16T15:00:00", "Clashes with booking #1"],
  ]);

  serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": { ok: true, data: { venue: grandHall(), updated_fields: ["setup_time_mins"], flagged_bookings: [] } },
  });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.setup_time_mins = "40";
  await state.saveVenueForm();
  assert.equal(state.venueWorkspace.outcome.bufferChanged, true);
  assert.equal(state.venueWorkspace.outcome.flagged.length, 0);
  assert.match(html(), /No bookings were newly flagged by this change\./);
});

test("TC-9209 Retire asks in the page, then lists the affected bookings, says they weren't cancelled, and the venue leaves the catalogue", async () => {
  const state = await staffWithCatalogue();
  const calls = serve(state, {
    "POST /venues/7/retire": { ok: true, data: { venue: { ...grandHall(), is_active: false }, affected_bookings: [
      { booking_id: 4, event_id: 2, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00", status: "Confirmed" },
      { booking_id: 5, event_id: 3, event_title: "Spring Fair", booking_start: "2026-11-16T13:30:00", booking_end: "2026-11-16T15:00:00", status: "Pending" },
    ] } },
    "GET /venues": { ok: true, data: [] },
  });
  state.askRetireVenue(grandHall());
  assert.equal(state.venueWorkspace.retire.venue.id, 7);
  assert.deepEqual(calls, []);
  await state.confirmRetireVenue();
  assert.equal(calls[0].method, "POST"); assert.equal(calls[0].path, "/venues/7/retire");
  assert.equal(state.venueWorkspace.retire, null);
  const outcome = plain(state.venueWorkspace.outcome);
  assert.equal(outcome.kind, "retired"); assert.equal(outcome.venueName, "Grand Hall");
  assert.deepEqual(outcome.affected.map(row => [row.eventTitle, row.start, row.end, row.status]), [
    ["Annual Gala", "2026-11-16T10:00:00", "2026-11-16T12:00:00", "Confirmed"],
    ["Spring Fair", "2026-11-16T13:30:00", "2026-11-16T15:00:00", "Pending"],
  ]);
  assert.equal(state.venueWorkspace.venues.some(venue => venue.id === 7), false);
  // The confirmation and the result both say the bookings aren't cancelled and leave Booking requests (D6).
  assert.match(html(), /won't be cancelled[^<]*Booking requests/);
  assert.match(html(), /weren't cancelled[^<]*Booking requests/);
  // An in-page confirmation, never a browser dialog.
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  for (const text of [source, html()]) assert.doesNotMatch(text, /\bconfirm\(/);
});

test("TC-9210 cancelling the confirmation sends nothing; a 409 on retire is shown in place", async () => {
  const state = await staffWithCatalogue();
  const calls = serve(state, {});
  state.askRetireVenue(grandHall());
  state.cancelRetireVenue();
  assert.equal(state.venueWorkspace.retire, null);
  assert.deepEqual(calls, []);
  const retired = "Venue 'Grand Hall' is already retired";
  serve(state, { "POST /venues/7/retire": () => { state.alert = { message: retired, kind: "error" }; return { ok: false, status: 409, data: { detail: retired } }; } });
  state.askRetireVenue(grandHall());
  await state.confirmRetireVenue();
  assert.equal(state.venueWorkspace.retire.error, retired);
  assert.equal(state.venueWorkspace.retire.busy, false);
  assert.equal(state.alert.message, "");
  assert.equal(state.venueWorkspace.outcome, null);
});

test("TC-9211 the catalogue shows each venue's facilities, layouts, accessibility, hours and buffers", async () => {
  const state = await staffWithCatalogue();
  assert.equal(state.venueBufferSummary(grandHall()), "30 min setup, 45 min turnaround");
  const markup = html();
  for (const binding of ["venue.facilities", "venue.supported_layouts", "venue.accessibility", "venue.operating_hours", "venueBufferSummary(venue)"]) {
    assert.ok(markup.includes(binding), binding);
  }
  // D8: every venue field has a label.
  for (const id of ["venue-name", "venue-location", "venue-capacity", "venue-facilities", "venue-accessibility",
    "venue-layouts", "venue-hours", "venue-setup", "venue-turnaround"]) {
    assert.match(markup, new RegExp(`<label for="${id}"`), id);
    assert.match(markup, new RegExp(`id="${id}"`), id);
  }
  assert.match(markup, /@click="toggleVenueCreate\(\)"/);
  assert.match(markup, /venueWorkspace\.formMessage/);
});

test("TC-9212 only Venue Staff can add, edit or retire venues", async () => {
  for (const role of ["lead", "coordinator", "organiser", "attendee", "technical_support"]) {
    const state = app(role);
    state.apiRequest = () => assert.fail(`request sent for ${role}`);
    state.venueWorkspace = { venues: [grandHall()], bookings: [], form: venueRecord.emptyVenueForm(), showCreate: true,
      editing: grandHall(), formMessage: null, retire: { venue: grandHall(), busy: false, error: "" }, outcome: null, saving: false, busy: false };
    await state.openVenueEdit(grandHall()); await state.saveVenueForm(); await state.updateVenue();
    state.askRetireVenue(grandHall()); await state.confirmRetireVenue();
  }
});

test("TC-9213 a response that arrives after logout doesn't reopen the form or refill the catalogue", async () => {
  const state = await staffWithCatalogue();
  let complete;
  state.apiRequest = () => new Promise(resolve => { complete = resolve; });
  const opening = state.openVenueEdit(grandHall());
  resetSignedOutState(state);
  complete({ ok: true, data: grandHall() }); await opening;
  assert.equal(state.venueWorkspace, null);

  const again = await staffWithCatalogue();
  serve(again, { "GET /venues/7": { ok: true, data: grandHall() } });
  await again.openVenueEdit(grandHall());
  again.venueWorkspace.form.capacity = "150";
  again.apiRequest = () => new Promise(resolve => { complete = resolve; });
  const saving = again.saveVenueForm();
  resetSignedOutState(again);
  complete({ ok: true, data: { venue: grandHall(), updated_fields: ["capacity"], flagged_bookings: [] } }); await saving;
  assert.equal(again.venueWorkspace, null);
  assert.equal(again.alert, null);
});

test("TC-9216 a save that doesn't change setup or turnaround shows no flagged-bookings section", async () => {
  const state = await staffWithCatalogue();
  serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": { ok: true, data: { venue: { ...grandHall(), name: "Great Hall" }, updated_fields: ["name"], flagged_bookings: [] } },
  });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.name = "Great Hall";
  await state.saveVenueForm();
  assert.equal(state.venueWorkspace.outcome.kind, "updated");
  assert.equal(state.venueWorkspace.outcome.venueName, "Great Hall");
  assert.equal(state.venueWorkspace.outcome.bufferChanged, false);
});

test("TC-9217 editing a venue that no longer exists doesn't open the form and refreshes the catalogue", async () => {
  const state = await staffWithCatalogue();
  const calls = serve(state, { "GET /venues/7": { ok: false, status: 404, data: { detail: "Venue not found" } } });
  await state.openVenueEdit(grandHall());
  assert.equal(state.venueWorkspace.editing, null);
  assert.equal(state.venueWorkspace.showCreate, false);
  assert.equal(state.alert.message, "Venue not found");
  assert.ok(calls.some(call => call.method === "GET" && call.path === "/venues"));
});

// The handler a button's @click calls, read from the markup, so the test presses what the page presses.
function handlerOf(markup, pattern) {
  const button = markup.match(pattern);
  assert.ok(button, String(pattern));
  return { tag: button[0], name: button[0].match(/@(?:click|submit\.prevent)="(\w+)\(/)[1] };
}

test("TC-9222 Add venue, × and Cancel do nothing while a save is in flight, so the save's result still lands", async () => {
  const markup = html();
  const buttons = [
    handlerOf(markup, /<button[^>]*@click="\w+\(\)"[^>]*><span aria-hidden="true">＋<\/span> Add venue<\/button>/),
    handlerOf(markup, /<button[^>]*aria-label="Close venue form"[^>]*>/),
    handlerOf(markup, /<button[^>]*@click="\w+\(\)"[^>]*>Cancel<\/button>(?=<button class="button button-primary" :disabled="venueWorkspace\.busy)/),
  ];
  // Add venue and × sit outside the form's fieldset, so they need their own disabled binding.
  const busy = await staffWithCatalogue();
  busy.venueWorkspace.saving = true;
  for (const { tag } of buttons.slice(0, 2)) assert.equal(disabledBy(busy, tag), true, tag);
  const press = state => { for (const { name } of buttons) state[name](); };

  // An edit that changes the turnaround: the PATCH is held open.
  const state = await staffWithCatalogue();
  let release;
  const calls = serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": () => new Promise(resolve => { release = resolve; }),
    "GET /venues": { ok: true, data: [{ ...grandHall(), turnaround_time_mins: 90 }] },
  });
  await state.openVenueEdit(grandHall());
  state.venueWorkspace.form.turnaround_time_mins = "90";
  const saving = state.saveVenueForm();
  assert.equal(state.venueWorkspace.saving, true);
  const typed = plain(state.venueWorkspace.form);
  press(state);
  assert.equal(state.venueWorkspace.showCreate, true);
  assert.equal(state.venueWorkspace.editing.id, 7);
  assert.deepEqual(plain(state.venueWorkspace.form), typed);
  release({ ok: true, data: { venue: { ...grandHall(), turnaround_time_mins: 90 }, updated_fields: ["turnaround_time_mins"], flagged_bookings: [
    { booking_id: 1, id: 1, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00", status: "Confirmed",
      conflict_flag: { conflicting_booking_id: 2, reason: "Clashes with booking #2", flagged_at: "2026-10-26T09:00:00" } },
  ] } });
  await saving;
  assert.deepEqual(plain(state.venueWorkspace.outcome.flagged).map(row => [row.eventTitle, row.reason]), [["Annual Gala", "Clashes with booking #2"]]);
  assert.equal(state.venueWorkspace.showCreate, false);
  const patchAt = calls.findIndex(call => call.method === "PATCH");
  assert.ok(calls.slice(patchAt + 1).some(call => call.method === "GET" && call.path === "/venues"), "catalogue refreshed");
  assert.equal(state.venueWorkspace.venues[0].turnaround_time_mins, 90);

  // Add venue: the POST is held open, then answered with a 422 that must land on the open form.
  const adding = await staffWithCatalogue([]);
  serve(adding, { "POST /venues": () => new Promise(resolve => { release = resolve; }) });
  adding.toggleVenueCreate();
  Object.assign(adding.venueWorkspace.form, { name: "Harbour Ballroom", location: "Block B", capacity: "250" });
  const creating = adding.saveVenueForm();
  const entered = plain(adding.venueWorkspace.form);
  press(adding);
  assert.equal(adding.venueWorkspace.showCreate, true);
  release({ ok: false, status: 422, data: { detail: [{ loc: ["body", "capacity"], msg: "Value error, Capacity must be greater than 0" }] } });
  await creating;
  assert.equal(adding.venueWorkspace.showCreate, true);
  assert.equal(adding.fieldErrors.capacity, "Capacity must be greater than 0");
  assert.deepEqual(plain(adding.venueWorkspace.form), entered);
});

// [TC-9223] One venue write at a time: the buttons whose handlers and :disabled bindings the tests use.
function venueButtons() {
  const markup = html();
  return {
    edit: handlerOf(markup, /<button[^>]*:aria-label="`Edit \$\{venue\.name\}`"[^>]*>/),
    retire: handlerOf(markup, /<button[^>]*:aria-label="`Retire \$\{venue\.name\}`"[^>]*>/),
    confirm: handlerOf(markup, /<button type="button" class="button button-danger"[^>]*>/),
    save: handlerOf(markup, /<form class="create-venue-card"[^>]*>/),
    fieldset: markup.match(/<form class="create-venue-card"[\s\S]*?(<fieldset[^>]*>)/)[1],
  };
}

const smallRoom = () => ({ ...grandHall(), id: 8, name: "Small Room", capacity: 20 });

// Evaluates a tag's :disabled expression against the state, the way Alpine does.
function disabledBy(state, tag) {
  const expression = tag.match(/:disabled="([^"]*)"/)?.[1];
  assert.ok(expression, `no :disabled on ${tag}`);
  return Boolean(new Function("scope", `with (scope) { return (${expression}); }`)(state));
}

test("TC-9223 while a save is in flight, Retire and Retire venue send nothing, and the save's flagged list still lands", async () => {
  const buttons = venueButtons();
  const state = await staffWithCatalogue([grandHall(), smallRoom()]);
  let release;
  const calls = serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "PATCH /venues/7": () => new Promise(resolve => { release = resolve; }),
    "GET /venues": { ok: true, data: [{ ...grandHall(), turnaround_time_mins: 90 }, smallRoom()] },
  });
  await state[buttons.edit.name](grandHall());
  state.venueWorkspace.form.turnaround_time_mins = "90";
  const saving = state[buttons.save.name]();
  for (const venue of [grandHall(), smallRoom()]) {
    state[buttons.retire.name](venue);
    await state[buttons.confirm.name]();
  }
  assert.deepEqual(calls.filter(call => call.path.endsWith("/retire")), []);
  assert.equal(state.venueWorkspace.retire, null);
  release({ ok: true, data: { venue: { ...grandHall(), turnaround_time_mins: 90 }, updated_fields: ["turnaround_time_mins"], flagged_bookings: [
    { booking_id: 1, id: 1, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00", status: "Confirmed",
      conflict_flag: { conflicting_booking_id: 2, reason: "Clashes with booking #2", flagged_at: "2026-10-26T09:00:00" } },
  ] } });
  await saving;
  assert.equal(state.venueWorkspace.outcome.kind, "updated");
  assert.deepEqual(plain(state.venueWorkspace.outcome.flagged).map(row => [row.eventTitle, row.reason]), [["Annual Gala", "Clashes with booking #2"]]);
  const patchAt = calls.findIndex(call => call.method === "PATCH");
  assert.ok(calls.slice(patchAt + 1).some(call => call.method === "GET" && call.path === "/venues"), "catalogue refreshed");
});

test("TC-9223 while a retire is in flight, Save and Edit send nothing, and the affected list still lands", async () => {
  const buttons = venueButtons();
  const state = await staffWithCatalogue([grandHall(), smallRoom()]);
  let release;
  const calls = serve(state, {
    "GET /venues/7": { ok: true, data: grandHall() },
    "POST /venues/8/retire": () => new Promise(resolve => { release = resolve; }),
    "GET /venues": { ok: true, data: [grandHall()] },
  });
  await state[buttons.edit.name](grandHall());
  state.venueWorkspace.form.capacity = "150";
  state[buttons.retire.name](smallRoom());
  const retiring = state[buttons.confirm.name]();
  const sentBefore = calls.length;
  await state[buttons.save.name]();
  for (const venue of [smallRoom(), grandHall()]) await state[buttons.edit.name](venue);
  assert.deepEqual(calls.slice(sentBefore), []);
  release({ ok: true, data: { venue: { ...smallRoom(), is_active: false }, affected_bookings: [
    { booking_id: 5, event_id: 3, event_title: "Spring Fair", booking_start: "2026-11-16T13:30:00", booking_end: "2026-11-16T15:00:00", status: "Pending" },
  ] } });
  await retiring;
  assert.equal(state.venueWorkspace.outcome.kind, "retired");
  assert.deepEqual(plain(state.venueWorkspace.outcome.affected).map(row => [row.eventTitle, row.status]), [["Spring Fair", "Pending"]]);
  assert.equal(state.venueWorkspace.venues.some(venue => venue.id === 8), false);
  // The edit of the other venue is still open, with what was typed.
  assert.equal(state.venueWorkspace.editing.id, 7);
  assert.equal(state.venueWorkspace.form.capacity, "150");
});

test("TC-9223 Retire and Retire venue are disabled while saving; the venue form and Edit are disabled while retiring", async () => {
  const { edit, retire, confirm, fieldset } = venueButtons();
  const state = await staffWithCatalogue();
  state.venueWorkspace.retire = { venue: grandHall(), busy: false, error: "" };
  for (const tag of [edit.tag, retire.tag, confirm.tag, fieldset]) assert.equal(disabledBy(state, tag), false, `idle: ${tag}`);
  state.venueWorkspace.saving = true;
  for (const tag of [retire.tag, confirm.tag]) assert.equal(disabledBy(state, tag), true, `saving: ${tag}`);
  state.venueWorkspace.saving = false;
  state.venueWorkspace.retire.busy = true;
  for (const tag of [fieldset, edit.tag]) assert.equal(disabledBy(state, tag), true, `retiring: ${tag}`);
});
