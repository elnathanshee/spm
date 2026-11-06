import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

// SCRUM-83: cancelling one of an event's bookings, and seeing it afterwards.
// Backend side: backend/tests/test_scrum83_multiple_venues.py.

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

const FUTURE = { booking_start: "2099-03-01T02:00:00", booking_end: "2099-03-01T06:00:00" };
const hall = { id: 1, venue_name: "Grand Hall", event_id: 7, status: "Pending", ...FUTURE };
const room = { id: 2, venue_name: "Room B", event_id: 7, status: "Confirmed", ...FUTURE };

function loadApp() {
  let factory;
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  });
  return factory();
}

// The booking dialog open on an event with two bookings; `listed` is what the next list fetch returns.
async function dialog(role = "Coordinator", assigned = true) {
  const state = loadApp();
  const event = { id: 7, title: "Gala", status: "Approved" };
  state.user = { user_id: 2, role };
  state.assignedEvents = assigned ? [event] : [];
  const ctx = { state, sent: [], listed: [hall, room], cancelResponse: { ok: true, status: 200, data: {} } };
  state.apiRequest = async (path, options = {}) => {
    if (options.method === "POST") { ctx.sent.push(path); return ctx.cancelResponse; }
    if (path === "/venues") return { ok: true, data: [] };
    return { ok: true, status: 200, data: ctx.listed.map(booking => ({ ...booking })) };
  };
  await state.openBookings(event);
  return ctx;
}

test("TC-8315 Venue Staff can filter to Cancelled bookings, which sit under Past and closed", () => {
  const state = loadApp();
  state.user = { user_id: 3, role: "venue_staff" };
  state.venueWorkspace = { bookingFilter: "Cancelled", bookings: [{ id: 1, status: "Cancelled" }, { id: 2, status: "Pending" }] };
  const chips = state.listControls("bookings").chips;
  assert.ok(chips.some(chip => chip.key === "Cancelled" && chip.count === 1), "a Cancelled chip with its count");
  assert.deepEqual(plain(state.filteredVenueBookings().map(booking => booking.id)), [1]);
  assert.equal(state.venueBookingGroupOf({ status: "Cancelled" }), "closed");
  assert.equal(state.bookingStatusClass("Cancelled"), state.bookingStatusClass("Expired"), "the same quiet badge as other closed bookings");
});

test("TC-8316 Cancel booking is offered only while the booking holds the venue, and only to whoever manages the event", async () => {
  const { state } = await dialog();
  assert.equal(state.canCancelBooking({ status: "Pending", ...FUTURE }), true);
  assert.equal(state.canCancelBooking({ status: "Confirmed", ...FUTURE }), true);
  for (const status of ["Rejected", "Expired", "Cancelled"]) assert.equal(state.canCancelBooking({ status, ...FUTURE }), false, status);
  assert.equal(state.canCancelBooking({ status: "Confirmed", booking_start: "2020-01-01T01:00:00", booking_end: "2020-01-01T02:00:00" }), false, "an ended booking");
  assert.equal((await dialog("Event Coordinator Lead", false)).state.canCancelBooking({ status: "Pending", ...FUTURE }), true);
  const notAssigned = await dialog();
  notAssigned.state.assignedEvents = [];
  assert.equal(notAssigned.state.canCancelBooking({ status: "Pending", ...FUTURE }), false);
});

test("TC-8317 Cancelling asks first: Keep booking sends nothing, and only confirming sends the cancel", async () => {
  const ctx = await dialog();
  const { state } = ctx;
  const booking = state.bookingDialog.bookings[0];
  state.askCancelBooking(booking);
  assert.equal(state.bookingDialog.cancelAsk, 1);
  assert.equal(ctx.sent.length, 0, "asking sends nothing");
  state.keepBooking();
  assert.equal(state.bookingDialog.cancelAsk, null);
  assert.equal(ctx.sent.length, 0, "keeping sends nothing");

  state.askCancelBooking(booking);
  ctx.listed = [{ ...hall, status: "Cancelled" }, room];
  await state.confirmCancelBooking(booking);
  assert.deepEqual(ctx.sent, ["/venues/bookings/1/cancel"]);
  assert.equal(state.bookingDialog.cancelAsk, null);
  assert.deepEqual(plain(state.bookingDialog.bookings.map(row => row.status)), ["Cancelled", "Confirmed"], "the row stays as history and the other booking is untouched");
  assert.equal(state.alert.kind, "success");
  assert.match(state.alert.message, /Grand Hall/);
});

test("TC-8318 A refused cancel shows the server's reason and refreshes the list", async () => {
  const ctx = await dialog();
  const { state } = ctx;
  const booking = state.bookingDialog.bookings[0];
  state.askCancelBooking(booking);
  ctx.cancelResponse = { ok: false, status: 400, data: { detail: "This booking no longer holds the venue." } };
  ctx.listed = [{ ...hall, status: "Expired" }, room];
  await state.confirmCancelBooking(booking);
  assert.equal(state.bookingDialog.cancelError, "This booking no longer holds the venue.");
  assert.equal(state.bookingDialog.cancelAsk, null);
  assert.equal(state.bookingDialog.cancelBusy, false);
  assert.equal(state.bookingDialog.bookings[0].status, "Expired");
});

test("TC-8319 The booking history has the Cancel booking action with an in-page confirm step", () => {
  const history = html.slice(html.indexOf('id="booking-history-title"'), html.indexOf('aria-labelledby="find-venues-title"'));
  assert.match(history, /x-if="canCancelBooking\(booking\)"/);
  assert.match(history, /@click="askCancelBooking\(booking\)"/);
  assert.match(history, /`Cancel the booking at \$\{booking\.venue_name\}`/);
  assert.match(history, /keepBooking\(\)/);
  assert.match(history, /confirmCancelBooking\(booking\)/);
  assert.match(history, /role="alert" x-show="bookingDialog\.cancelError"/);
  assert.doesNotMatch(source, /window\.confirm/, "never a browser dialog");
});
