import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
function app(role, fetch) {
  let factory;
  const context = {
    fetch,
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
  test(`${role === "coordinator" ? "TC-3516" : "TC-3517"} ${role} uses the shared interface, existing APIs and complete booking information`, async () => {
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
    assert.equal(request.options.body.booking_start, new Date(form.booking_start).toISOString());
    assert.equal(request.options.body.booking_end, new Date(form.booking_end).toISOString());
    assert.equal(state.alert.kind, "success");
    assert.equal(state.bookingDialog.bookings[0].rejection_reason, booking.rejection_reason);
    assert.equal(state.bookingDialog.busy, false);
    assert.equal(state.bookingDialog.form.venue_id, "");
  });
}

test("TC-3518 unrelated roles and unassigned Coordinator events cannot open or submit bookings", async () => {
  for (const role of ["organiser", "venue_staff", "attendee", "technical_support", "admin", "coordinator"]) {
    const state = app(role);
    state.apiRequest = () => assert.fail("unauthorized API call");
    await state.openBookings(state.leadEvents[0]);
    assert.equal(state.bookingDialog, null);
    assert.equal(state.canBookEvent(state.leadEvents[0]), false);
  }
});

test("TC-3519 validation, rejected API requests and busy state preserve the form", async () => {
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

test("TC-3520 failed retrieval disables submission and logout discards pending booking state", async () => {
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
  test(`${assigned ? "TC-3522" : "TC-3521"} Lead ${assigned ? "reassignment" : "assignment"} still uses the existing API`, async () => {
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

test("TC-3523 one shared dialog safely renders status and complete rejection messages without decision controls", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.equal((html.match(/id="booking-title"/g) || []).length, 1);
  assert.equal((html.match(/@click="openBookings\(event\)"/g) || []).length, 2);
  assert.match(html, /x-text="booking.rejection_reason"/);
  assert.match(html, /x-text="booking.status"/);
  const dialog = html.slice(html.indexOf('<template x-if="bookingDialog">'), html.indexOf('<template x-if="modal.open">'));
  assert.doesNotMatch(dialog, /approve|rejectBooking|reason.*input/i);
});


test("TC-6426 requesters keep seeing their returned notifications after assignment changes", async () => {
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
  test(`${role === "coordinator" ? "TC-3524" : "TC-3525"} ${role}: empty venues and failed history do not disable date/time fields`, async () => {
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

test("TC-3526 date/time fieldset depends only on submission, with independent empty and error states", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /<fieldset :disabled="bookingDialog.submitting">/);
  assert.doesNotMatch(html, /<fieldset :disabled="bookingDialog.busy/);
  assert.match(html, /No venues available. Please contact Venue Staff to add a venue./);
  assert.match(html, /Booking history could not be loaded/);
  assert.match(html, /!bookingDialog.form.venue_id/);
});


test("TC-3527 non-JSON venue/history responses never enable invalid booking state", async () => {
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


test("TC-3528 HTTP failures identify the request endpoint", () => {
  const state = app("lead");
  state.handleHttpError(500, {}, "/events/8/bookings");
  assert.ok(state.alert.message.includes("/events/8/bookings"));
  assert.ok(state.alert.message.includes("500"));
});

async function holdApp(role = "coordinator") {
  const state = app(role);
  const event = role === "lead" ? state.leadEvents[0] : state.assignedEvents[0];
  const booking = { id: 10, event_id: event.id, venue_name: "Hall", status: "Pending", hold_expires_at: "2099-04-10T09:00:00", warning_minutes: 60 };
  state.apiRequest = async path => ({ ok: true, data: path === "/venues" ? [{ id: 3 }] : [booking] });
  await state.openBookings(event);
  return { state, booking };
}

test("TC-9501 hold presentation uses returned fields, UTC interpretation and explicit expired styling", async () => {
  const { state } = await holdApp();
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const dialog = html.slice(html.indexOf('<template x-if="bookingDialog">'), html.indexOf('<template x-if="modal.open">'));
  const row = dialog.slice(dialog.indexOf('<ul class="booking-list">'), dialog.indexOf('</ul>'));
  assert.match(row, /formatHoldExpiry\(booking.hold_expires_at\)/);
  assert.match(row, /booking.warning_minutes == null/);
  assert.match(row, /x-if="canUpdateHoldWarning\(booking\)"/);
  assert.match(row, /@submit.prevent="updateHoldWarning\(booking\)"/);
  assert.match(row, /x-model="bookingDialog.warningInputs\[booking.id\]"/);
  assert.match(row, /Save warning/);
  const detailsStart = row.indexOf('<div x-show="booking.status');
  const controlsStart = row.indexOf('<template x-if="canUpdateHoldWarning');
  assert.ok(detailsStart >= 0 && detailsStart < controlsStart);
  assert.match(row.slice(detailsStart, controlsStart), /Hold expiry:.*Warning:/);
  assert.doesNotMatch(html.slice(0, html.indexOf('<template x-if="bookingDialog">')), /hold-warning-|formatHoldExpiry\(booking.hold_expires_at\)/);
  assert.equal(state.bookingStatusClass("Expired"), "status-rejected");
  assert.equal(state.bookingStatusClass("Confirmed"), "status-approved");
  assert.equal(state.formatHoldExpiry(null), "Not set");
  assert.equal(state.formatHoldExpiry("invalid"), "Not set");
  const previousTZ = process.env.TZ;
  try {
    process.env.TZ = "Asia/Singapore";
    assert.equal(state.formatHoldExpiry("2099-04-10T09:00:00"), state.formatDate("2099-04-10T09:00:00Z"));
    assert.notEqual(state.formatHoldExpiry("2099-04-10T09:00:00"), state.formatDate("2099-04-10T09:00:00"));
    assert.equal(state.formatHoldExpiry("2099-04-10T09:00:00+08:00"), state.formatDate("2099-04-10T09:00:00+08:00"));
  } finally {
    if (previousTZ === undefined) delete process.env.TZ;
    else process.env.TZ = previousTZ;
  }
});

for (const minutes of ["0", "75"]) {
  test(`TC-9502 warning ${minutes} uses PUT and merges without a GET`, async () => {
    const { state, booking } = await holdApp();
    const list = state.bookingDialog.bookings;
    state.bookingDialog.warningInputs[10] = minutes;
    let calls = 0;
    state.apiRequest = async (path, options) => {
      calls++;
      assert.equal(path, "/venues/bookings/10/hold/warning");
      assert.equal(options.method, "PUT");
      assert.equal(options.body.warning_minutes, Number(minutes));
      return { ok: true, data: { booking: { id: 10, warning_minutes: Number(minutes) } } };
    };
    await state.updateHoldWarning(booking);
    assert.equal(calls, 1);
    assert.equal(booking.warning_minutes, Number(minutes));
    assert.equal(booking.venue_name, "Hall");
    assert.equal(state.bookingDialog.bookings, list);
    assert.equal(state.bookingDialog.warningBusy, null);
    assert.equal(state.alert.kind, "success");
  });
}

test("TC-9503 invalid inputs remain editable without affecting booking form errors", async () => {
  const { state, booking } = await holdApp();
  state.fieldErrors = { venue_id: "Select a venue" };
  state.apiRequest = () => assert.fail("invalid input submitted");
  for (const value of ["-1", "1.5", "", "abc", " ", "Infinity", "9007199254740992"]) {
    state.bookingDialog.warningInputs[10] = value;
    await state.updateHoldWarning(booking);
    assert.ok(state.bookingDialog.warningErrors[10]);
    assert.equal(state.bookingDialog.warningInputs[10], value);
    assert.equal(state.fieldErrors.venue_id, "Select a venue");
    assert.equal(state.bookingDialog.submitting, false);
  }
});

test("TC-9504 only assigned Coordinators can update live Pending rows", async () => {
  for (const scenario of ["lead", "unassigned", "expired", "elapsed", "confirmed", "wrong-event", "foreign-row"]) {
    const { state, booking } = await holdApp(scenario === "lead" ? "lead" : "coordinator");
    if (scenario === "unassigned") state.assignedEvents = [];
    if (scenario === "expired") booking.status = "Expired";
    if (scenario === "elapsed") booking.hold_expires_at = "2000-01-01T00:00:00";
    if (scenario === "confirmed") booking.status = "Confirmed";
    if (scenario === "wrong-event") booking.event_id = 999;
    if (scenario === "foreign-row") state.bookingDialog.bookings = [];
    state.apiRequest = () => assert.fail("unauthorized update");
    assert.equal(state.canUpdateHoldWarning(booking), false);
    await state.updateHoldWarning(booking);
  }
});

for (const status of [403, 409, 422]) {
  test(`TC-9505 ${status} preserves input and row, clears busy and presents feedback`, async () => {
    const { state, booking } = await holdApp();
    state.bookingDialog.warningInputs[10] = "90";
    const detail = status === 422 ? [{ loc: ["body", "warning_minutes"], msg: "Invalid warning" }] : "This hold expired; submit a new request";
    state.apiRequest = async () => ({ ok: false, status, data: { detail } });
    await state.updateHoldWarning(booking);
    assert.equal(booking.warning_minutes, 60);
    assert.equal(state.bookingDialog.warningInputs[10], "90");
    assert.equal(state.bookingDialog.warningBusy, null);
    if (status === 422) assert.equal(state.bookingDialog.warningErrors[10], "Invalid warning");
    else {
      assert.equal(state.canUpdateHoldWarning(booking), false);
      assert.match(state.alert.message, status === 403 ? /permission/ : /This hold expired/);
      await state.updateHoldWarning(booking);
    }
    assert.notEqual(state.alert.kind, "success");
    assert.equal(state.bookingDialog.submitting, false);
  });
}

test("TC-9506 duplicate warning submissions make one request", async () => {
  const { state, booking } = await holdApp();
  let resolve;
  let calls = 0;
  state.apiRequest = () => { calls++; return new Promise(done => { resolve = done; }); };
  const pending = state.updateHoldWarning(booking);
  await state.updateHoldWarning(booking);
  assert.equal(calls, 1);
  resolve({ ok: true, data: { booking: { warning_minutes: 60 } } });
  await pending;
  assert.equal(state.bookingDialog.warningBusy, null);
});

for (const action of ["close", "replace", "logout", "switch"]) {
  test(`TC-9507 late success and failure after ${action} cannot alter current state`, async () => {
    for (const ok of [true, false]) {
      const { state, booking } = await holdApp();
      state.bookingDialog.warningInputs[10] = "90";
      let resolve;
      state.apiRequest = () => new Promise(done => { resolve = done; });
      const pending = state.updateHoldWarning(booking);
      if (action === "close") state.closeBookings();
      if (action === "replace") state.bookingDialog = { marker: true };
      if (action === "logout") resetSignedOutState(state);
      if (action === "switch") state.user = { user_id: 99, role: "Coordinator" };
      state.showAlert("Current message", "success");
      resolve({ ok, status: 409, data: { booking: { warning_minutes: 90 }, detail: "Stale error" } });
      await pending;
      assert.equal(booking.warning_minutes, 60);
      assert.equal(state.alert.message, "Current message");
    }
  });
}

test("TC-9508 deferred API errors leave shared form state intact", async () => {
  for (const status of [403, 409, 422]) {
    const state = app("coordinator", async (_, options) => {
      assert.equal(options.deferErrors, undefined);
      assert.equal(JSON.parse(options.body).warning_minutes, 0);
      return { ok: false, status, json: async () => ({ detail: [{ loc: ["body", "warning_minutes"], msg: "Invalid" }] }) };
    });
    state.fieldErrors = { venue_id: "Keep this" };
    const result = await state.apiRequest("/venues/bookings/10/hold/warning", { method: "PUT", body: { warning_minutes: 0 }, deferErrors: true });
    assert.equal(result.status, status);
    assert.equal(state.alert.message, "");
    assert.equal(state.fieldErrors.venue_id, "Keep this");
  }
});

test("TC-9509 shared history retains hold metadata for Coordinator and read-only Lead", async () => {
  for (const role of ["coordinator", "lead"]) {
    const { state, booking } = await holdApp(role);
    assert.equal(state.bookingDialog.bookings[0], booking);
    assert.equal(booking.hold_expires_at, "2099-04-10T09:00:00");
    assert.equal(booking.warning_minutes, 60);
    assert.notEqual(state.formatHoldExpiry(booking.hold_expires_at), "Not set");
    assert.equal(state.bookingDialog.warningInputs[10], "60");
    assert.equal(state.canUpdateHoldWarning(booking), role === "coordinator");
    if (role === "lead") {
      state.apiRequest = () => assert.fail("Lead warning update");
      await state.updateHoldWarning(booking);
    }
  }
});

test("TC-9510 Pending rows without hold metadata show unavailable values and cannot edit", async () => {
  const { state, booking } = await holdApp();
  booking.hold_expires_at = null;
  booking.warning_minutes = null;
  assert.equal(state.formatHoldExpiry(booking.hold_expires_at), "Not set");
  assert.equal(state.canUpdateHoldWarning(booking), false);
  state.apiRequest = () => assert.fail("missing hold metadata submitted");
  await state.updateHoldWarning(booking);
  // An expiry alone is not sufficient warning metadata either.
  booking.hold_expires_at = "2099-04-10T09:00:00";
  assert.equal(state.canUpdateHoldWarning(booking), false);
  await state.updateHoldWarning(booking);
  assert.equal(booking.warning_minutes, null);
});

test("TC-9511 dialog Refresh displays the backend Expired status and removes warning eligibility", async () => {
  const { state, booking } = await holdApp();
  const event = state.bookingDialog.event;
  assert.equal(state.canUpdateHoldWarning(booking), true);
  const expired = { ...booking, status: "Expired" };
  const calls = [];
  state.apiRequest = async path => {
    calls.push(path);
    return { ok: true, data: path === "/venues" ? [{ id: 3 }] : [expired] };
  };
  await state.openBookings(event);
  assert.equal(calls.filter(path => path === `/events/${event.id}/bookings`).length, 1);
  assert.equal(state.bookingDialog.bookings[0].status, "Expired");
  assert.equal(state.bookingStatusClass(state.bookingDialog.bookings[0].status), "status-rejected");
  assert.equal(state.canUpdateHoldWarning(state.bookingDialog.bookings[0]), false);
});

function inSingapore(run) {
  const previousTZ = process.env.TZ;
  process.env.TZ = "Asia/Singapore";
  return Promise.resolve().then(run).finally(() => {
    if (previousTZ === undefined) delete process.env.TZ;
    else process.env.TZ = previousTZ;
  });
}

test("TC-9519 booking request sends the local times as UTC", () => inSingapore(async () => {
  const { state } = await holdApp();
  let body;
  state.apiRequest = async (path, options) => {
    if (options?.method === "POST") body = options.body;
    return { ok: true, data: path.startsWith("/events/") ? [] : {} };
  };
  Object.assign(state.bookingDialog.form, form);
  await state.submitBooking();
  assert.equal(body.booking_start, "2099-04-10T02:00:00.000Z");
  assert.equal(body.booking_end, "2099-04-11T04:00:00.000Z");
}));

test("TC-9520 stored booking times are shown as UTC in local time", () => inSingapore(async () => {
  const { state } = await holdApp();
  assert.equal(state.formatSchedule("2099-04-10T02:00:00", "2099-04-10T04:00:00"),
    `${state.formatDate("2099-04-10T10:00")} – ${state.formatDate("2099-04-10T12:00")}`);
  assert.equal(state.formatSchedule("2099-04-10T02:00:00+00:00", null), `${state.formatDate("2099-04-10T10:00")} – Not set`);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.equal((html.match(/formatSchedule\(/g) || []).length, 3);
}));

test("TC-9521 server messages show Singapore periods as written and UTC expiries in local time", () => inSingapore(async () => {
  const { state } = await holdApp();
  const local = value => state.formatDate(value);
  // The backend writes booked periods in Singapore time (no label) and hold expiries in UTC.
  const notice = state.formatNotificationMessage({ message: 'The hold on Hall for "Gala" (2099-04-10 10:00 - 12:00) now expires at 2099-04-09 14:18 UTC.' });
  assert.equal(notice, `The hold on Hall for "Gala" (${local("2099-04-10T10:00")} – ${state.formatTime("2099-04-10T12:00")}) now expires at ${local("2099-04-09T22:18")}.`);
  const overnight = state.formatNotificationMessage({ message: "Clashes with booking #4 (2099-04-10 23:00 - 01:00)" });
  assert.equal(overnight, `Clashes with booking #4 (${local("2099-04-10T23:00")} – ${local("2099-04-11T01:00")})`);
  const decision = state.formatNotificationMessage({ message: "from 2099-04-10 10:00:00 to 2099-04-10 12:00:00 was approved." });
  assert.equal(decision, `from ${local("2099-04-10T10:00")} to ${local("2099-04-10T12:00")} was approved.`);
  state.handleHttpError(409, { detail: "This hold expired at 2099-04-09 14:18 UTC. Submit a new request." }, "/venues/bookings/10/hold/warning");
  assert.equal(state.alert.message, `This hold expired at ${local("2099-04-09T22:18")}. Submit a new request.`);
  state.handleHttpError(409, { detail: "Direct conflict with existing booking (2099-04-10 10:00 - 12:00)" }, "/venues/3/bookings");
  assert.equal(state.alert.message, `Direct conflict with existing booking (${local("2099-04-10T10:00")} – ${state.formatTime("2099-04-10T12:00")})`);
  state.handleHttpError(500, {}, "/venues/3/bookings");
  assert.equal(state.alert.message, "Request to /venues/3/bookings failed with status 500.");
}));

test("TC-9522 saving a warning on a hold that expired while the dialog was open explains why", async () => {
  const { state, booking } = await holdApp();
  state.bookingDialog.warningInputs[10] = "30";
  booking.hold_expires_at = "2000-01-01T00:00:00";
  state.apiRequest = () => assert.fail("expired hold submitted");
  await state.updateHoldWarning(booking);
  assert.equal(state.alert.kind, "error");
  assert.equal(state.alert.message, "This hold has expired. Click Refresh to see its status.");
  assert.equal(state.canUpdateHoldWarning(booking), false);
  assert.equal(state.bookingDialog.warningInputs[10], "30");
  assert.equal(booking.warning_minutes, 60);
  // Someone who may not edit the hold still gets no message.
  const lead = await holdApp("lead");
  lead.booking.hold_expires_at = "2000-01-01T00:00:00";
  lead.state.apiRequest = () => assert.fail("Lead warning update");
  await lead.state.updateHoldWarning(lead.booking);
  assert.equal(lead.state.alert.message, "");
});
