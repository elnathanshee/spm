import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
function app(role) {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() },
    Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 2, role };
  state.assignedEvents = [{ id: 7, title: "Assigned event" }];
  state.leadEvents = [{ id: 8, title: "Lead event" }];
  return state;
}
const form = { venue_id: "3", booking_start: "2099-04-10T10:00", booking_end: "2099-04-11T12:00" };

for (const role of ["coordinator", "lead"]) {
  test(`${role} uses the shared interface, existing APIs and complete booking information`, async () => {
    const state = app(role);
    const event = role === "lead" ? state.leadEvents[0] : state.assignedEvents[0];
    const booking = { id: 10, venue_name: "Hall", status: "Rejected", rejection_reason: "Reason ".repeat(500) };
    const calls = [];
    state.apiRequest = async (path, options) => {
      calls.push({ path, options });
      return { ok: true, data: path === "/venues" ? [{ id: 3, name: "Hall" }] : path.startsWith("/events/") ? [booking] : {} };
    };
    assert.equal(state.canBookEvent(event), true);
    await state.openBookings(event);
    assert.equal(state.bookingDialog.ready, true);
    assert.equal(state.bookingDialog.bookings[0], booking);
    Object.assign(state.bookingDialog.form, form);
    await state.submitBooking();
    const request = calls.find(call => call.options?.method === "POST");
    assert.equal(request.path, "/venues/3/bookings");
    assert.equal(request.options.body.event_id, event.id);
    assert.equal(request.options.body.booking_start, form.booking_start);
    assert.equal(request.options.body.booking_end, form.booking_end);
    assert.equal(state.alert.kind, "success");
    assert.equal(state.bookingDialog.bookings[0].rejection_reason, booking.rejection_reason);
    assert.equal(state.bookingDialog.busy, false);
    assert.equal(state.bookingDialog.form.venue_id, "");
  });
}

test("unrelated roles and unassigned Coordinator events cannot open or submit bookings", async () => {
  for (const role of ["organiser", "venue_staff", "attendee", "technical_support", "admin", "coordinator"]) {
    const state = app(role);
    state.apiRequest = () => assert.fail("unauthorized API call");
    await state.openBookings(state.leadEvents[0]);
    assert.equal(state.bookingDialog, null);
    assert.equal(state.canBookEvent(state.leadEvents[0]), false);
  }
});

test("validation, rejected API requests and busy state preserve the form", async () => {
  const state = app("lead");
  state.apiRequest = async path => ({ ok: true, data: path === "/venues" ? [{ id: 3 }] : [] });
  await state.openBookings(state.leadEvents[0]);
  state.apiRequest = () => assert.fail("invalid form submitted");
  await state.submitBooking();
  assert.ok(state.fieldErrors.venue_id);
  assert.ok(state.fieldErrors.booking_start);
  Object.assign(state.bookingDialog.form, form, { booking_end: "2099-04-09T10:00" });
  await state.submitBooking();
  assert.ok(state.fieldErrors.booking_end);
  Object.assign(state.bookingDialog.form, form);
  let complete;
  state.apiRequest = () => new Promise(resolve => { complete = resolve; });
  const pending = state.submitBooking();
  assert.equal(state.bookingDialog.busy, true);
  await state.submitBooking();
  complete({ ok: false });
  await pending;
  assert.equal(state.bookingDialog.form.venue_id, "3");
  assert.equal(state.bookingDialog.busy, false);
});

test("failed retrieval disables submission and logout discards pending booking state", async () => {
  const state = app("lead");
  state.apiRequest = async () => ({ ok: false });
  await state.openBookings(state.leadEvents[0]);
  assert.equal(state.bookingDialog.ready, false);
  state.apiRequest = () => assert.fail("submission after failed load");
  await state.submitBooking();
  const completions = [];
  state.apiRequest = () => new Promise(resolve => completions.push(resolve));
  const pending = state.openBookings(state.leadEvents[0]);
  resetSignedOutState(state);
  completions.forEach(resolve => resolve({ ok: true, data: [{ id: 99 }] }));
  await pending;
  assert.equal(state.bookingDialog, null);
});

for (const assigned of [false, true]) {
  test(`Lead ${assigned ? "reassignment" : "assignment"} still uses the existing API`, async () => {
    const state = app("lead");
    let refreshed = false;
    state.fetchLeadData = async () => { refreshed = true; };
    state.apiRequest = async (path, options) => {
      assert.equal(path, `/events/8/${assigned ? "reassign" : "assign"}`);
      assert.equal(options.body.coordinator_id, 5);
      return { ok: true };
    };
    await state.saveAssignment({ id: 8, selectedCoordinatorId: "5", assigned_coordinator_id: assigned ? 4 : null });
    assert.equal(refreshed, true);
    assert.equal(state.alert.kind, "success");
  });
}

test("one shared dialog safely renders status and complete rejection messages without decision controls", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.equal((html.match(/id="booking-title"/g) || []).length, 1);
  assert.equal((html.match(/@click="openBookings\(event\)"/g) || []).length, 2);
  assert.match(html, /x-text="booking.rejection_reason"/);
  assert.match(html, /x-text="booking.status"/);
  const dialog = html.slice(html.indexOf('<template x-if="bookingDialog">'), html.indexOf('<template x-if="modal.open">'));
  assert.doesNotMatch(dialog, /approve|rejectBooking|reason.*input/i);
});


test("requesters keep seeing their returned notifications after assignment changes", async () => {
  for (const role of ["coordinator", "lead"]) {
    const state = app(role);
    state.assignedEvents = [];
    state.leadEvents = [];
    const messages = [{ id: 42, kind: "booking_rejected", message: "Hall booking rejected. Reason: unavailable" }];
    state.apiRequest = async path => {
      assert.equal(path, "/notifications");
      return { ok: true, data: messages };
    };
    await state.fetchNotifications();
    assert.equal(state.notifications, messages);
  }
});


for (const role of ["coordinator", "lead"]) {
  test(`${role}: empty venues and failed history do not disable date/time fields`, async () => {
    const state = app(role);
    const event = role === "lead" ? state.leadEvents[0] : state.assignedEvents[0];
    state.apiRequest = async path => path === "/venues" ? { ok: true, data: [] } : { ok: false };
    await state.openBookings(event);
    assert.equal(state.bookingDialog.ready, true);
    assert.equal(state.bookingDialog.bookingsLoaded, false);
    assert.equal(state.bookingDialog.submitting, false);
    assert.equal(state.bookingDialog.busy, false);
    Object.assign(state.bookingDialog.form, form, { venue_id: "" });
    state.apiRequest = () => assert.fail("no venue selected");
    await state.submitBooking();
    assert.ok(state.fieldErrors.venue_id);
    // After Venue Staff add a venue, the existing refresh loads it.
    state.apiRequest = async path => path === "/venues" ? { ok: true, data: [{ id: 3, name: "New hall" }] } : { ok: false };
    await state.openBookings(event);
    assert.equal(state.bookingDialog.venues[0].name, "New hall");
    Object.assign(state.bookingDialog.form, form);
    let submitted = false;
    state.apiRequest = async (path, options) => {
      if (options?.method === "POST") { submitted = true; return { ok: true }; }
      return { ok: true, data: [] };
    };
    await state.submitBooking();
    assert.equal(submitted, true);
    assert.equal(state.bookingDialog.submitting, false);
  });
}

test("date/time fieldset depends only on submission, with independent empty and error states", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /<fieldset :disabled="bookingDialog.submitting">/);
  assert.doesNotMatch(html, /<fieldset :disabled="bookingDialog.busy/);
  assert.match(html, /No venues available. Please contact Venue Staff to add a venue./);
  assert.match(html, /Booking history could not be loaded/);
  assert.match(html, /!bookingDialog.form.venue_id/);
});


test("non-JSON venue/history responses never enable invalid booking state", async () => {
  const state = app("lead");
  // apiRequest parses the Vite HTML fallback as {}; HTTP 200 alone is insufficient.
  state.apiRequest = async () => ({ ok: true, data: {} });
  await state.openBookings(state.leadEvents[0]);
  assert.equal(state.bookingDialog.ready, false);
  assert.equal(state.bookingDialog.bookingsLoaded, false);
  assert.equal(state.bookingDialog.busy, false);
  assert.equal(state.bookingDialog.submitting, false);
  assert.ok(state.alert.message.includes("invalid response"));
  state.apiRequest = () => assert.fail("invalid venue response must not be submitted");
  await state.submitBooking();
});


test("HTTP failures identify the request endpoint", () => {
  const state = app("lead");
  state.handleHttpError(500, {}, "/events/8/bookings");
  assert.ok(state.alert.message.includes("/events/8/bookings"));
  assert.ok(state.alert.message.includes("500"));
});
