// SCRUM-82: the Venue Staff form for unavailability periods, the Coordinator's
// flag in the booking list, and the calendar's unavailable periods.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole } from "../src/rbac.js";
import * as venueRecord from "../src/venue-record.js";
import { venueUnavailability } from "../src/venue-unavailability.js";

const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const MAIN = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");

const PERIOD = { id: 5, venue_id: 7, start_at: "2099-06-01T10:00:00", end_at: "2099-06-01T12:00:00", reason: "Maintenance" };

// The component with a stubbed API: routes maps "METHOD path" to a response.
function panel(routes = {}) {
  const calls = [];
  const state = venueUnavailability({ id: 7, name: "Grand Hall" });
  state.refreshed = 0;
  state.fetchVenueWorkspace = async () => { state.refreshed += 1; };
  state.apiRequest = async (path, options = {}) => {
    const key = `${options.method || "GET"} ${path}`;
    calls.push({ key, options });
    const route = routes[key];
    if (typeof route === "function") return route();
    return route || { ok: true, status: 200, data: [] };
  };
  return { state, calls };
}

// app.js in a Node vm, as the other screen tests load it.
function app(role = "coordinator") {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], requestedFrontendPath: () => null, normalizeRoleName: normalizeRole,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} }, ...venueRecord,
  });
  const state = factory();
  state.user = { user_id: 3, role };
  return state;
}

test("TC-8240 the panel lists the venue's periods from the backend", async () => {
  const { state, calls } = panel({ "GET /venues/7/unavailability": { ok: true, status: 200, data: [PERIOD] } });
  await state.load();
  assert.deepEqual(calls.map(call => call.key), ["GET /venues/7/unavailability"]);
  assert.equal(calls[0].options.inlineErrors, true);
  assert.deepEqual(state.periods, [PERIOD]);
  assert.equal(state.loading, false);
  assert.equal(state.loadError, "");
});

test("TC-8241 a list that fails to load says so, and keeps what was shown", async () => {
  const { state } = panel({ "GET /venues/7/unavailability": { ok: false, status: 500, data: { detail: "Database unavailable" } } });
  state.periods = [PERIOD];
  await state.load();
  assert.match(state.loadError, /could not be loaded/i);
  assert.deepEqual(state.periods, [PERIOD]);
  assert.equal(state.loading, false);
});

test("TC-8242 blank fields and an end before the start are caught before anything is sent", async () => {
  const { state, calls } = panel();
  await state.save();
  assert.ok(state.errors.start);
  assert.ok(state.errors.end);
  assert.ok(state.errors.reason);
  Object.assign(state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T18:00", reason: "   " });
  await state.save();
  assert.match(state.errors.end, /after the start/);
  assert.ok(state.errors.reason);
  assert.equal(calls.length, 0);
});

test("TC-8243 saving sends Singapore times with their offset, then shows the affected bookings", async () => {
  const affected = [{ booking_id: 11, event_title: "Annual Symposium", status: "Confirmed", booking_start: "2099-06-01T10:30:00", booking_end: "2099-06-01T11:00:00" }];
  const { state, calls } = panel({
    "POST /venues/7/unavailability": { ok: true, status: 201, data: { period: PERIOD, affected_bookings: affected } },
    "GET /venues/7/unavailability": { ok: true, status: 200, data: [PERIOD] },
  });
  Object.assign(state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T20:00", reason: "  Maintenance  " });
  await state.save();
  const post = calls.find(call => call.key.startsWith("POST"));
  assert.deepEqual(post.options.body, { start_at: "2099-06-01T18:00:00+08:00", end_at: "2099-06-01T20:00:00+08:00", reason: "Maintenance" });
  assert.deepEqual(state.affected, affected);
  assert.match(state.message.text, /1 booking is flagged/);
  assert.deepEqual(state.periods, [PERIOD]);
  assert.deepEqual(state.form, { start_at: "", end_at: "", reason: "" });
  assert.equal(state.busy, false);
  assert.equal(state.refreshed, 1, "the booking lists are refreshed so the new flags show");
});

test("TC-8244 a period that affects nothing says so", async () => {
  const { state } = panel({ "POST /venues/7/unavailability": { ok: true, status: 201, data: { period: PERIOD, affected_bookings: [] } } });
  Object.assign(state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T20:00", reason: "Safety" });
  await state.save();
  assert.match(state.message.text, /No bookings are affected/);
});

test("TC-8245 a refused save shows the backend's message and keeps what was typed", async () => {
  const detail = "This period is already recorded for the venue";
  const { state } = panel({ "POST /venues/7/unavailability": { ok: false, status: 409, data: { detail } } });
  Object.assign(state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T20:00", reason: "Safety" });
  await state.save();
  assert.equal(state.message.kind, "error");
  assert.equal(state.message.text, detail);
  assert.equal(state.form.reason, "Safety");
  assert.equal(state.refreshed, 0);

  const invalid = panel({ "POST /venues/7/unavailability": { ok: false, status: 422, data: { detail: [{ msg: "Value error, The end must be after the start" }] } } });
  Object.assign(invalid.state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T20:00", reason: "Safety" });
  await invalid.state.save();
  assert.equal(invalid.state.message.text, "The end must be after the start");
});

test("TC-8246 a second press while saving sends nothing more", async () => {
  let finish;
  const { state, calls } = panel({ "POST /venues/7/unavailability": () => new Promise(resolve => { finish = resolve; }) });
  Object.assign(state.form, { start_at: "2099-06-01T18:00", end_at: "2099-06-01T20:00", reason: "Safety" });
  const first = state.save();
  await state.save();
  assert.equal(calls.filter(call => call.key.startsWith("POST")).length, 1);
  finish({ ok: true, status: 201, data: { period: PERIOD, affected_bookings: [] } });
  await first;
  assert.equal(state.busy, false);
});

test("TC-8247 removing a period reloads the list and refreshes the bookings", async () => {
  let removed = false;
  const { state, calls } = panel({
    "DELETE /venues/7/unavailability/5": () => { removed = true; return { ok: true, status: 200, data: {} }; },
    "GET /venues/7/unavailability": () => ({ ok: true, status: 200, data: removed ? [] : [PERIOD] }),
  });
  await state.load();
  await state.remove(PERIOD);
  assert.ok(calls.some(call => call.key === "DELETE /venues/7/unavailability/5"));
  assert.deepEqual(state.periods, []);
  assert.match(state.message.text, /removed/i);
  assert.equal(state.refreshed, 1);
  assert.equal(state.removingId, null);
});

test("TC-8248 a refused removal keeps the period and says why", async () => {
  const { state } = panel({
    "GET /venues/7/unavailability": { ok: true, status: 200, data: [PERIOD] },
    "DELETE /venues/7/unavailability/5": { ok: false, status: 404, data: { detail: "Unavailability period not found" } },
  });
  await state.load();
  await state.remove(PERIOD);
  assert.deepEqual(state.periods, [PERIOD]);
  assert.equal(state.message.kind, "error");
  assert.equal(state.message.text, "Unavailability period not found");
  assert.equal(state.removingId, null);
});

test("TC-8249 the panel sits in the venue record, labelled, with a live status, and is registered", () => {
  const start = HTML.indexOf('x-data="venueUnavailability(venue)"');
  assert.ok(start > HTML.indexOf('class="venue-record"'), "inside the venue record");
  assert.ok(start < HTML.indexOf('id="venue-list-title"') + 6000, "in the Venue Staff catalogue");
  for (const field of ["start", "end", "reason"]) {
    assert.match(HTML, new RegExp(`<label :for="\\\`unavailable-${field}-\\$\\{venue\\.id\\}\\\`"`));
    assert.match(HTML, new RegExp(`:id="\\\`unavailable-${field}-\\$\\{venue\\.id\\}\\\`"`));
  }
  assert.match(HTML, /aria-live="polite"[^>]*x-text="message\?\.text"/);
  assert.match(HTML, /@click="remove\(period\)"/);
  assert.match(MAIN, /import \{ venueUnavailability \} from "\.\/venue-unavailability\.js";/);
  assert.match(MAIN, /Alpine\.data\("venueUnavailability", venueUnavailability\);/);
});

test("TC-8250 the Coordinator's booking list shows the flag and a way to request another venue", () => {
  const list = HTML.slice(HTML.indexOf('id="booking-history-title"'), HTML.indexOf('id="find-venues-title"'));
  assert.match(list, /x-show="booking\.conflict_flag"/);
  assert.match(list, /x-text="booking\.conflict_flag\?\.reason"/);
  assert.match(list, /Alternative arrangements needed/);
  assert.match(list, /@click="startReplacementRequest\(booking\)"[^>]*>Request another venue</);
});

test("TC-8254 focus stays in the panel after Remove, even though the pressed button has gone", async () => {
  let buttonGone = false;
  let focused = 0;
  const heading = { focus() { focused += 1; } };
  const { state } = panel({ "DELETE /venues/7/unavailability/5": () => { buttonGone = true; return { ok: true, status: 204, data: null }; } });
  // Alpine finds refs from the element that was clicked, so once that button is out
  // of the page the ref can't be found any more.
  Object.defineProperty(state, "$refs", { get: () => (buttonGone ? {} : { unavailabilityHeading: heading }) });
  await state.remove(PERIOD);
  assert.equal(focused, 1);
});

test("TC-8255 Venue Staff see a period's flag as arrangements needed, and a real clash as a possible clash", () => {
  const row = HTML.slice(HTML.indexOf('<td data-label="Booking" class="booking-cell-main">'), HTML.indexOf('<td data-label="Date and time"'));
  assert.match(row, /x-if="booking\.conflict_flag && !booking\.conflict_flag\.unavailability_id"><p class="booking-row-note"><span class="row-flag">Possible clash</);
  assert.match(row, /x-if="booking\.conflict_flag\?\.unavailability_id"><p class="booking-row-note"><span class="row-flag">Alternative arrangements needed</);
  const review = HTML.match(/<div class="conflict-callout" role="note" x-show="staffBooking\.detail\.conflict_flag">.*?<\/div>/)?.[0] || "";
  assert.match(review, /<strong x-text="staffBooking\.detail\.conflict_flag\?\.unavailability_id \? 'Alternative arrangements needed' : 'Possible clash'"><\/strong>/);
});

test("TC-8256 each venue's reason list has its own id, so the page has no duplicate ids", () => {
  assert.doesNotMatch(HTML, / id="unavailable-reasons"| list="unavailable-reasons"/);
  assert.match(HTML, /:list="`unavailable-reasons-\$\{venue\.id\}`"/);
  assert.match(HTML, /<datalist :id="`unavailable-reasons-\$\{venue\.id\}`">/);
});

test("TC-8251 Request another venue fills the search and the request with the flagged times, in Singapore time", async () => {
  const state = app();
  state.assignedEvents = [{ id: 9 }];
  state.apiRequest = async () => ({ ok: true, status: 200, data: [] });
  await state.openBookings({ id: 9, title: "Annual Symposium" });
  state.startReplacementRequest({ booking_start: "2099-06-01T10:00:00", booking_end: "2099-06-01T12:30:00" });
  const dialog = state.bookingDialog;
  assert.equal(dialog.form.booking_start, "2099-06-01T18:00");
  assert.equal(dialog.form.booking_end, "2099-06-01T20:30");
  assert.equal(dialog.search.start_time, "2099-06-01T18:00");
  assert.equal(dialog.search.end_time, "2099-06-01T20:30");
  assert.equal(dialog.search.available_only, true);
  assert.equal(dialog.form.venue_id, "", "the Coordinator still chooses the venue");
});

test("TC-8252 the calendar lists unavailable periods, and a blocked day doesn't read as free", () => {
  const state = app("venue_staff");
  assert.equal(state.calendarDayIsFree({ date: "2099-06-01", busy: [], unavailable: [PERIOD] }), false);
  assert.equal(state.calendarDayIsFree({ date: "2099-06-02", busy: [], unavailable: [] }), true);
  assert.equal(state.calendarDayIsFree({ date: "2099-06-03", busy: [] }), true, "a day without the field is still free");
  const calendar = HTML.slice(HTML.indexOf('id="venue-calendar-title"'), HTML.indexOf('x-if="bookingDialog"'));
  assert.match(calendar, /x-for="period in venueCalendar\.data\.unavailable_periods"/);
  assert.match(calendar, /x-text="period\.reason"/);
  assert.match(calendar, /formatSchedule\(period\.start_at, period\.end_at\)/);
  assert.match(calendar, /x-for="period in day\.unavailable \|\| \[\]"/);
});
