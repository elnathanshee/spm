import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

function app(role = "venue_staff") {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], requestedFrontendPath: () => null, normalizeRoleName: normalizeRole,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
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
