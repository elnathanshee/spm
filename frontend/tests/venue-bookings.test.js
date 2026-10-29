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
    assert.equal(request.options.body.booking_start, form.booking_start);
    assert.equal(request.options.body.booking_end, form.booking_end);
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

// ============================================================================
// SCRUM-91 (Sprint 4): Venue Search & Suitability UI (TC-9101 to TC-9105)
// ============================================================================

test("TC-9101 (AC1): venue search filters build query and display availability and clash reasons", async () => {
  const state = app("coordinator");
  const event = state.assignedEvents[0];
  state.apiRequest = async path => {
    if (path === "/venues") return { ok: true, data: [{ id: 1, name: "Hall A" }] };
    if (path.startsWith("/events/")) return { ok: true, data: [] };
    return { ok: true, data: [] };
  };
  await state.openBookings(event);

  assert.ok(state.bookingDialog.search, "search filter state should exist");
  state.bookingDialog.search.start_time = "2026-11-20T09:00";
  state.bookingDialog.search.end_time = "2026-11-20T17:00";
  state.bookingDialog.search.min_capacity = 150;
  state.bookingDialog.search.location = "Building 2";
  state.bookingDialog.search.accessibility = "Wheelchair";
  state.bookingDialog.search.layout = "Theatre";
  state.bookingDialog.search.facilities = "Projector, Audio";
  state.bookingDialog.search.available_only = true;

  let requestedPath = null;
  state.apiRequest = async path => {
    requestedPath = path;
    return {
      ok: true,
      data: [
        { id: 1, name: "Grand Hall", capacity: 200, is_available: true, conflict_reason: null },
        { id: 2, name: "Meeting Room B", capacity: 50, is_available: false, conflict_reason: "Direct conflict with booking #102" }
      ]
    };
  };

  await state.searchVenues();

  assert.ok(requestedPath.startsWith("/venues/search"));
  assert.ok(requestedPath.includes("start_time=2026-11-20T09%3A00") || requestedPath.includes("start_time=2026-11-20T09:00"));
  assert.ok(requestedPath.includes("end_time=2026-11-20T17%3A00") || requestedPath.includes("end_time=2026-11-20T17:00"));
  assert.ok(requestedPath.includes("min_capacity=150"));
  assert.ok(requestedPath.includes("location=Building+2") || requestedPath.includes("location=Building%202"));
  assert.ok(requestedPath.includes("accessibility=Wheelchair"));
  assert.ok(requestedPath.includes("layout=Theatre"));
  assert.ok(requestedPath.includes("available_only=true"));

  assert.equal(state.bookingDialog.searchResults.length, 2);
  assert.equal(state.bookingDialog.searchResults[0].is_available, true);
  assert.equal(state.bookingDialog.searchResults[1].is_available, false);
  assert.equal(state.bookingDialog.searchResults[1].conflict_reason, "Direct conflict with booking #102");
});

test("TC-9102 (AC2): suitability panel checks venue capacity and entered facilities", async () => {
  const state = app("coordinator");
  const event = state.assignedEvents[0];
  state.apiRequest = async () => ({ ok: true, data: [] });
  await state.openBookings(event);

  let suitabilityPath = null;
  state.apiRequest = async path => {
    suitabilityPath = path;
    return {
      ok: true,
      data: [
        {
          venue_id: 1,
          venue_name: "Grand Hall",
          suitability: "suitable",
          checks: [
            { check: "capacity", status: "pass", detail: "Capacity 200 meets expected 100" },
            { check: "facilities", status: "pass", detail: "Has Projector, Audio" }
          ]
        },
        {
          venue_id: 2,
          venue_name: "Small Annex",
          suitability: "unsuitable",
          checks: [
            { check: "capacity", status: "fail", detail: "Capacity 50 below expected 100" },
            { check: "facilities", status: "pass", detail: "Has Projector" }
          ]
        },
        {
          venue_id: 3,
          venue_name: "Studio 1",
          suitability: "unknown",
          checks: [
            { check: "capacity", status: "pass", detail: "Capacity 120 meets expected 100" },
            { check: "facilities", status: "unknown", detail: "Venue facilities not recorded" }
          ]
        }
      ]
    };
  };

  await state.checkSuitability("Projector, Audio");

  assert.ok(suitabilityPath.includes(`/events/${event.id}/venue-suitability`));
  assert.equal(state.bookingDialog.suitabilityResults.length, 3);
  assert.equal(state.bookingDialog.suitabilityResults[0].suitability, "suitable");
  assert.equal(state.bookingDialog.suitabilityResults[1].suitability, "unsuitable");
  assert.equal(state.bookingDialog.suitabilityResults[2].suitability, "unknown");
});

test("TC-9103 (AC3): request on result prefills form and displays 409 clash in dialog", async () => {
  const state = app("coordinator");
  const event = state.assignedEvents[0];
  state.apiRequest = async () => ({ ok: true, data: [] });
  await state.openBookings(event);

  state.bookingDialog.search.start_time = "2026-11-20T10:00";
  state.bookingDialog.search.end_time = "2026-11-20T14:00";

  const targetVenue = { id: 5, name: "Auditorium" };
  state.selectVenueFromSearch(targetVenue);

  assert.equal(state.bookingDialog.form.venue_id, "5");
  assert.equal(state.bookingDialog.form.booking_start, "2026-11-20T10:00");
  assert.equal(state.bookingDialog.form.booking_end, "2026-11-20T14:00");

  // Submit booking that triggers 409 conflict
  state.bookingDialog.venues = [targetVenue];
  state.apiRequest = async (path, options) => {
    if (options?.method === "POST") {
      return {
        ok: false,
        status: 409,
        data: { detail: "Cannot approve: turnaround clash with booking #40" }
      };
    }
    return { ok: true, data: [] };
  };

  await state.submitBooking();

  // Clash error must appear in the dialog (dialog.error), not behind it
  assert.equal(state.bookingDialog.error, "Cannot approve: turnaround clash with booking #40");
});

test("TC-9104 (AC4): half-entered period (400) and empty search results show clear messages", async () => {
  const state = app("coordinator");
  const event = state.assignedEvents[0];
  state.apiRequest = async () => ({ ok: true, data: [] });
  await state.openBookings(event);

  // 1. Half-entered period (start without end)
  state.bookingDialog.search.start_time = "2026-11-20T10:00";
  state.bookingDialog.search.end_time = "";
  state.apiRequest = async () => ({
    ok: false,
    status: 400,
    data: { detail: "Provide both start_time and end_time, or neither" }
  });

  await state.searchVenues();
  assert.equal(state.bookingDialog.searchError, "Provide both start_time and end_time, or neither");

  // 2. Empty results
  state.bookingDialog.search.start_time = "2026-11-20T10:00";
  state.bookingDialog.search.end_time = "2026-11-20T12:00";
  state.apiRequest = async () => ({ ok: true, data: [] });

  await state.searchVenues();
  assert.equal(state.bookingDialog.searchResults.length, 0);
  assert.ok(state.bookingDialog.searchMessage.includes("No venues found") || state.bookingDialog.searchMessage.includes("No venues matched"));
});

test("TC-9105 (AC5): Lead can search/check for any event; Coordinator only for assigned (403 handled)", async () => {
  // 1. Lead can book and check any event
  const leadState = app("lead");
  leadState.apiRequest = async () => ({ ok: true, data: [] });
  const anyEvent = { id: 99, title: "Unassigned event", assigned_coordinator_id: 999 };
  assert.equal(leadState.canBookEvent(anyEvent), true);

  // 2. Coordinator can only book assigned events
  const coordState = app("coordinator");
  coordState.assignedEvents = [{ id: 7, title: "Assigned event", assigned_coordinator_id: coordState.user.user_id }];
  assert.equal(coordState.canBookEvent(coordState.assignedEvents[0]), true);
  assert.equal(coordState.canBookEvent(anyEvent), false);

  // 3. 403 on suitability is handled with error message in dialog
  await coordState.openBookings(coordState.assignedEvents[0]);
  coordState.apiRequest = async () => ({
    ok: false,
    status: 403,
    data: { detail: "You can only check venue suitability for events assigned to you" }
  });
  await coordState.checkSuitability("Projector");
  assert.equal(coordState.bookingDialog.suitabilityError, "You can only check venue suitability for events assigned to you");
});
