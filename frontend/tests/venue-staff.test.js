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

// ---------------------------------------------------------------------------
// SCRUM-89: review a booking with full context and manage its hold.
// Backend side (requester name, review data, hold rules): TC-8901 to TC-8903
// in backend/tests/test_scrum89_booking_review.py.
// ---------------------------------------------------------------------------

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

// A datetime-local input value (local time, minute precision) for a Date.
function localInput(date) {
  const pad = n => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// The backend sends naive UTC times (no "Z"), e.g. "2026-11-05T10:00:00".
function naiveUtc(date) { return date.toISOString().slice(0, 19); }

// app() with a stubbed fetch, so apiRequest's own error handling runs.
function appWithFetch(handler, role = "venue_staff") {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], requestedFrontendPath: () => null, normalizeRoleName: normalizeRole,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
    fetch: async (url, request) => {
      const { status, body } = await handler(url, request);
      return { ok: status >= 200 && status < 300, status, json: async () => body };
    },
  });
  const state = factory(); state.user = { user_id: 3, role }; return state;
}

function pendingReview(overrides = {}) {
  const start = new Date(Date.now() + 10 * DAY);
  return { id: 9, detail: { id: 9, status: "Pending", booking_start: naiveUtc(start), booking_end: naiveUtc(new Date(start.getTime() + 2 * HOUR)) },
    reason: "", busy: false, holdExpiry: "", error: "", start, ...overrides };
}

test("TC-8904 review dialog shows the venue's attributes, the hold's expiry and warning, and the conflict flag", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const dialog = html.slice(html.indexOf('aria-labelledby="staff-booking-title"'));
  for (const field of ["venue?.capacity", "venue?.supported_layouts", "venue?.accessibility", "venue?.facilities",
    "hold_expires_at", "warning_minutes", "conflict_flag"]) {
    assert.ok(dialog.includes(`staffBooking.detail.${field}`), `dialog should show ${field}`);
  }
});

test("TC-8905 hold form validates the new expiry and sends PUT /venues/bookings/{id}/hold", async () => {
  const state = app();
  state.staffBooking = pendingReview();
  const { start } = state.staffBooking;
  state.apiRequest = () => assert.fail("an invalid expiry must not be sent");

  await state.setStaffHold(); assert.ok(state.fieldErrors.holdExpiry, "empty expiry is refused");
  state.staffBooking.holdExpiry = localInput(new Date(Date.now() - HOUR));
  await state.setStaffHold(); assert.match(state.fieldErrors.holdExpiry, /future/i);
  state.staffBooking.holdExpiry = localInput(new Date(start.getTime() + HOUR));
  await state.setStaffHold(); assert.match(state.fieldErrors.holdExpiry, /start/i);

  const valid = localInput(new Date(Date.now() + 2 * DAY));
  state.staffBooking.holdExpiry = valid;
  let sent;
  state.apiRequest = async (path, options) => {
    sent = { path, options };
    return { ok: true, status: 200, data: { booking: { ...state.staffBooking.detail, hold_expires_at: "2026-11-01T09:00:00" } } };
  };
  state.fetchVenueWorkspace = async () => {};
  await state.setStaffHold();
  assert.equal(sent.path, "/venues/bookings/9/hold");
  assert.equal(sent.options.method, "PUT");
  assert.equal(sent.options.body.expires_at, new Date(valid).toISOString());
  assert.equal(state.staffBooking.detail.hold_expires_at, "2026-11-01T09:00:00");
  assert.equal(state.staffBooking.holdExpiry, "");
  assert.equal(state.staffBooking.error, "");
  assert.equal(state.fieldErrors.holdExpiry, undefined);
});

test("TC-8906 hold 409 and 422 errors appear in the dialog, and the shared error area reads any error shape", async () => {
  const state = app();
  state.staffBooking = pendingReview({ holdExpiry: localInput(new Date(Date.now() + 2 * DAY)) });
  const calls = [];
  state.apiRequest = async (path, options) => {
    calls.push({ path, options });
    if (options?.method === "PUT") return { ok: false, status: 409, data: { detail: "This hold expired at 2026-10-30 10:00 UTC. The Coordinator must submit a new request." } };
    return { ok: true, status: 200, data: { ...state.staffBooking.detail, status: "Expired" } };
  };
  await state.setStaffHold();
  assert.equal(calls[0].options.inlineErrors, true, "the dialog handles this request's errors itself");
  assert.match(state.staffBooking.error, /hold expired/);
  assert.equal(state.staffBooking.detail.status, "Expired", "detail refreshed after the 409");

  state.staffBooking = pendingReview({ holdExpiry: localInput(new Date(Date.now() + 2 * DAY)) });
  state.apiRequest = async () => ({ ok: false, status: 422, data: { detail: "The new expiry can't be later than the booking's start (2026-11-05 10:00)" } });
  await state.setStaffHold();
  assert.match(state.staffBooking.error, /booking's start/);

  // Shared helper, reused by SCRUM-91 and SCRUM-95.
  assert.equal(state.dialogErrorMessage({ data: { detail: "Plain message" } }), "Plain message");
  assert.equal(state.dialogErrorMessage({ data: { detail: [{ loc: ["body", "expires_at"], msg: "Input should be a valid datetime" }] } }), "Input should be a valid datetime");
  assert.ok(state.dialogErrorMessage({ status: 500, data: {} }).length > 0, "a fallback message when there is no detail");

  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /x-text="staffBooking\.error"/);
  assert.match(html, /setStaffHold\(\)/);
});

test("TC-8907 an approve 409 appears inside the dialog, not in the page alert, and the detail refreshes", async () => {
  const state = appWithFetch(url => url.endsWith("/approve")
    ? { status: 409, body: { detail: "Cannot approve: the venue is already booked for booking #4" } }
    : { status: 200, body: { id: 9, status: "Pending", conflict_flag: { conflicting_booking_id: 4, reason: "Overlaps #4" } } });
  state.staffBooking = { id: 9, detail: { id: 9, status: "Pending" }, reason: "", busy: false, error: "" };
  await state.decideStaffBooking("approve");
  assert.match(state.staffBooking.error, /Cannot approve/);
  assert.equal(state.alert.message, "", "the page alert behind the dialog stays empty");
  assert.equal(state.staffBooking.detail.conflict_flag.conflicting_booking_id, 4, "detail refreshed");
  assert.equal(state.staffBooking.busy, false);
});

test("TC-8908 reject errors appear in the dialog, clear on success, and a 401 still signs the user out", async () => {
  let rejectStatus = 409;
  const state = appWithFetch(url => {
    if (url.endsWith("/reject")) return rejectStatus === 409
      ? { status: 409, body: { detail: "This hold expired at 2026-10-30 10:00 UTC. The Coordinator must submit a new request." } }
      : { status: 401, body: { detail: "Not authenticated" } };
    if (url.endsWith("/approve")) return { status: 200, body: { booking: { id: 9, status: "Confirmed" } } };
    return { status: 200, body: url === "/venues" ? [] : { id: 9, status: "Pending" } };
  });
  state.staffBooking = { id: 9, detail: { id: 9, status: "Pending" }, reason: "Clash", busy: false, error: "" };
  await state.decideStaffBooking("reject");
  assert.match(state.staffBooking.error, /hold expired/);
  assert.equal(state.alert.message, "");

  await state.decideStaffBooking("approve");
  assert.equal(state.staffBooking.error, "", "a successful decision clears the old error");
  assert.equal(state.staffBooking.detail.status, "Confirmed");

  state.staffBooking = { id: 9, detail: { id: 9, status: "Pending" }, reason: "Clash", busy: false, error: "" };
  rejectStatus = 401;
  await state.decideStaffBooking("reject");
  assert.equal(state.user, null, "an expired session still signs out");
});

test("TC-8909 booking list has an Expired filter with a count and an Expired badge", () => {
  const state = app();
  state.venueWorkspace = { bookingFilter: "Expired", bookings: [
    { id: 1, status: "Pending" }, { id: 2, status: "Expired" }, { id: 3, status: "Expired" }, { id: 4, status: "Confirmed" },
  ] };
  assert.deepEqual(Array.from(state.filteredVenueBookings(), booking => booking.id), [2, 3]);
  assert.equal(state.bookingFilterCount("Expired"), 2);
  const expiredClass = state.bookingStatusClass("Expired");
  assert.notEqual(expiredClass, "status-neutral", "Expired has its own badge style");
  assert.notEqual(expiredClass, state.bookingStatusClass("Pending"));
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /x-for="filter in \[[^\]]*'Expired'[^\]]*\]"/);
});

test("TC-8910 the requester is shown by name, with a fallback when none was recorded", () => {
  const state = app();
  assert.equal(state.requesterLabel({ requester_id: 2, requester_name: "coord_sarah" }), "coord_sarah");
  assert.equal(state.requesterLabel({ requester_id: null, requester_name: null }), "Not recorded");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /requesterLabel\(staffBooking\.detail\)/);
  assert.doesNotMatch(html, /Requester #/);
});
