/**
 * SCRUM-96: UI - view a venue's availability calendar.
 *
 * Acceptance criteria:
 *   AC1  A "Calendar" action, from the Venue Staff catalogue and from the
 *        booking dialog, opens a day-by-day view for a chosen range
 *        (default 7 days, at most 31).
 *   AC2  Each day shows the operating hours and the busy blocks, with booked
 *        and occupied times and status. Free days say so.
 *   AC3  A range over 31 days, or "to" before "from", shows the server's 422
 *        message.
 *   AC4  No organiser details are shown, and other roles cannot reach the view.
 *
 * Completes SCRUM-32 AC1 to AC4. Backend: GET /venues/{id}/calendar.
 *
 * Decisions recorded here:
 *   D1  The range is not validated in the browser. AC3 asks for the server's
 *       message, so the request goes out and the 422 detail is shown as given.
 *       One rule, enforced in one place.
 *   D2  Errors are shown inside the dialog (inlineErrors), not on the page
 *       alert, matching the booking dialog (SCRUM-89).
 *   D3  The occupied times come from the venue's setup and turnaround buffers
 *       (Week 7 Change 1). The browser renders what the server sends and does
 *       no arithmetic of its own.
 *   D4  Blocks carry an event id but never an organiser, so AC4 holds as long
 *       as the view renders only the fields the server sends.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { NAVIGATION, normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");

function app(role = "Venue Staff") {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(
    source
      .trimStart()
      .replace(/^const API_BASE.*$/m, 'const API_BASE = "";')
      .replace(/^import[\s\S]*?from ".*?";/gm, ""),
    {
      NAVIGATION,
      normalizeRoleName: normalizeRole,
      resolveRolePath,
      resetSignedOutState,
      requestedFrontendPath: () => null,
      document: { addEventListener: (_, fn) => fn() },
      Alpine: { data: (_, fn) => (factory = fn) },
      window: { location: {}, setInterval: () => 0 },
    },
  );
  const state = factory();
  state.user = { user_id: 9, username: "venue1", role };
  return state;
}

const VENUE = { id: 4, name: "Grand Hall", location: "Block A", capacity: 200 };

/** A response shaped exactly like GET /venues/{id}/calendar. */
function calendarPayload(overrides = {}) {
  return {
    venue_id: 4,
    name: "Grand Hall",
    operating_hours: "08:00-22:00",
    setup_time_mins: 30,
    turnaround_time_mins: 45,
    from: "2026-11-09",
    to: "2026-11-15",
    unavailable_periods: [],
    days: [
      {
        date: "2026-11-09",
        operating_hours: "08:00-22:00",
        busy: [
          {
            event_id: 12,
            status: "Confirmed",
            booked_start: "2026-11-09T10:00:00",
            booked_end: "2026-11-09T12:00:00",
            occupied_start: "2026-11-09T09:30:00",
            occupied_end: "2026-11-09T12:45:00",
          },
        ],
      },
      { date: "2026-11-10", operating_hours: "08:00-22:00", busy: [] },
    ],
    ...overrides,
  };
}

/** Drive openVenueCalendar with a stubbed API and return [state, calls]. */
async function openWith(state, response) {
  const calls = [];
  state.apiRequest = async (path, options) => {
    calls.push([path, options]);
    return response;
  };
  await state.openVenueCalendar(VENUE);
  return calls;
}

// ---------------------------------------------------------------------------
// AC1 - the action, and the default range
// ---------------------------------------------------------------------------

test("TC-9601 | Happy Path | AC1: opening the calendar asks for a 7-day range from today", async () => {
  const state = app();
  const calls = await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  assert.equal(calls.length, 1);
  const [path, options] = calls[0];
  assert.match(path, /^\/venues\/4\/calendar\?/);

  const query = new URLSearchParams(path.split("?")[1]);
  const from = query.get("from");
  const to = query.get("to");
  assert.match(from, /^\d{4}-\d{2}-\d{2}$/, "from is a plain date");
  assert.match(to, /^\d{4}-\d{2}-\d{2}$/, "to is a plain date");

  // Inclusive 7 days means the last day is six days after the first.
  const span = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000;
  assert.equal(span, 6, "default range covers 7 days inclusive");

  // D2: the dialog shows its own errors rather than the page alert.
  assert.equal(options?.inlineErrors, true);
  assert.equal(state.venueCalendar.venue.id, 4);
  assert.equal(state.venueCalendar.busy, false);
});

test("TC-9602 | Happy Path | AC1, AC2: the server's days are held for rendering, with venue context", async () => {
  const state = app();
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  const calendar = state.venueCalendar;
  assert.equal(calendar.error, "");
  assert.equal(calendar.data.name, "Grand Hall");
  assert.equal(calendar.data.operating_hours, "08:00-22:00");
  assert.equal(calendar.data.setup_time_mins, 30);
  assert.equal(calendar.data.turnaround_time_mins, 45);
  assert.equal(calendar.days.length, 2);
  assert.equal(calendar.days[0].busy[0].status, "Confirmed");
});

test("TC-9603 | Workflow Variation | AC1: a changed range refetches with the chosen dates", async () => {
  const state = app();
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  const calls = [];
  state.apiRequest = async (path, options) => {
    calls.push(path);
    return { ok: true, status: 200, data: calendarPayload({ from: "2026-12-01", to: "2026-12-05" }) };
  };
  state.venueCalendar.from = "2026-12-01";
  state.venueCalendar.to = "2026-12-05";
  await state.fetchVenueCalendar();

  assert.equal(calls.length, 1);
  const query = new URLSearchParams(calls[0].split("?")[1]);
  assert.equal(query.get("from"), "2026-12-01");
  assert.equal(query.get("to"), "2026-12-05");
  assert.equal(state.venueCalendar.data.from, "2026-12-01");
});

// ---------------------------------------------------------------------------
// AC2 - what each day shows
// ---------------------------------------------------------------------------

test("TC-9604 | Happy Path | AC2: a day with a booking reports it, a day without is free", async () => {
  const state = app();
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  const [busyDay, freeDay] = state.venueCalendar.days;
  assert.equal(state.calendarDayIsFree(busyDay), false);
  assert.equal(state.calendarDayIsFree(freeDay), true);
  assert.equal(state.calendarDayLabel(freeDay.date).length > 0, true);
});

test("TC-9605 | Boundary Path | AC2 (D3): booked and occupied times are both shown, and differ by the buffers", async () => {
  const state = app();
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  const block = state.venueCalendar.days[0].busy[0];
  const booked = state.formatSchedule(block.booked_start, block.booked_end);
  const occupied = state.formatSchedule(block.occupied_start, block.occupied_end);
  assert.ok(booked.length > 0);
  assert.ok(occupied.length > 0);
  assert.notEqual(booked, occupied, "the occupied window includes setup and turnaround");
});

test("TC-9606 | Boundary Path | AC2: a booking over midnight is shown on both of its days", async () => {
  const overnight = {
    event_id: 20,
    status: "Confirmed",
    booked_start: "2026-11-09T22:00:00",
    booked_end: "2026-11-10T02:00:00",
    occupied_start: "2026-11-09T21:30:00",
    occupied_end: "2026-11-10T02:45:00",
  };
  const state = app();
  await openWith(state, {
    ok: true,
    status: 200,
    data: calendarPayload({
      days: [
        { date: "2026-11-09", operating_hours: "08:00-22:00", busy: [overnight] },
        { date: "2026-11-10", operating_hours: "08:00-22:00", busy: [overnight] },
      ],
    }),
  });

  const days = state.venueCalendar.days;
  assert.equal(days.every((day) => day.busy.some((b) => b.event_id === 20)), true);
  assert.equal(days.every((day) => state.calendarDayIsFree(day) === false), true);
});

test("TC-9607 | Boundary Path | AC2: a venue with no recorded operating hours says so rather than showing nothing", async () => {
  const state = app();
  await openWith(state, {
    ok: true,
    status: 200,
    data: calendarPayload({
      operating_hours: null,
      days: [{ date: "2026-11-09", operating_hours: null, busy: [] }],
    }),
  });

  assert.equal(state.calendarHours(state.venueCalendar.days[0]), "Not recorded");
});

// ---------------------------------------------------------------------------
// AC3 - the server's refusals
// ---------------------------------------------------------------------------

test("TC-9608 | Negative Path | AC3 (D1, D2): a 422 is shown as the server worded it, inside the dialog", async () => {
  for (const detail of [
    "The calendar covers at most 31 days; this range is 32 days",
    "'to' cannot be before 'from'",
  ]) {
    const state = app();
    let alerted = false;
    state.showAlert = () => (alerted = true);
    await openWith(state, { ok: false, status: 422, data: { detail } });

    assert.equal(state.venueCalendar.error, detail, "the server's wording is shown unchanged");
    assert.equal(state.venueCalendar.days.length, 0, "no stale days are left on screen");
    assert.equal(state.venueCalendar.busy, false);
    assert.equal(alerted, false, "the page alert is not used for a dialog error");
  }
});

test("TC-9609 | Negative Path | AC3: an over-long range is still sent, so the server decides", async () => {
  const state = app();
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });

  const calls = [];
  state.apiRequest = async (path) => {
    calls.push(path);
    return { ok: false, status: 422, data: { detail: "The calendar covers at most 31 days; this range is 90 days" } };
  };
  state.venueCalendar.from = "2026-11-01";
  state.venueCalendar.to = "2027-01-29";
  await state.fetchVenueCalendar();

  assert.equal(calls.length, 1, "the browser does not block the request itself");
  assert.match(state.venueCalendar.error, /at most 31 days/);
});

test("TC-9610 | Negative Path | AC3: an unknown or retired venue reports the server's 404", async () => {
  const state = app();
  await openWith(state, { ok: false, status: 404, data: { detail: "Venue not found" } });
  assert.equal(state.venueCalendar.error, "Venue not found");
  assert.equal(state.venueCalendar.days.length, 0);
});

test("TC-9611 | Negative Path | AC3: an unreachable backend still leaves a readable message", async () => {
  const state = app();
  await openWith(state, { ok: false, status: 0, data: {} });
  assert.ok(state.venueCalendar.error.length > 0, "a fallback message is shown when the server sends none");
});

// ---------------------------------------------------------------------------
// AC4 - who can see it, and what it never shows
// ---------------------------------------------------------------------------

test("TC-9612 | Cross-Cutting | AC4: only internal planning roles can open the calendar", async () => {
  for (const role of ["Venue Staff", "Coordinator", "Event Coordinator Lead"]) {
    const state = app(role);
    assert.equal(state.canViewVenueCalendar(), true, role);
    await openWith(state, { ok: true, status: 200, data: calendarPayload() });
    assert.ok(state.venueCalendar, `${role} should see the calendar`);
  }

  for (const role of ["Organiser", "Attendee", "Technical Support"]) {
    const state = app(role);
    assert.equal(state.canViewVenueCalendar(), false, role);
    state.apiRequest = () => assert.fail(`${role} must not reach the calendar endpoint`);
    await state.openVenueCalendar(VENUE);
    assert.equal(state.venueCalendar, null, `${role} should get no calendar`);
  }
});

test("TC-9613 | Cross-Cutting | AC4 (D4): no organiser or attendee detail is rendered", () => {
  const block = calendarPayload().days[0].busy[0];
  assert.deepEqual(
    Object.keys(block).sort(),
    ["booked_end", "booked_start", "event_id", "occupied_end", "occupied_start", "status"],
    "the server sends no organiser field, so none can be shown",
  );

  // Only the calendar's own markup, which ends where the booking dialog begins.
  const start = HTML.indexOf('x-if="venueCalendar"');
  const end = HTML.indexOf('x-if="bookingDialog"', start);
  assert.ok(start > -1 && end > start, "the calendar view is rendered before the booking dialog");
  const dialog = HTML.slice(start, end).toLowerCase();

  for (const forbidden of ["organiser", "requester", "attendee", "event_title"]) {
    assert.equal(dialog.includes(forbidden), false, `${forbidden} must not appear in the calendar view`);
  }
});

// ---------------------------------------------------------------------------
// The screen itself
// ---------------------------------------------------------------------------

test("TC-9614 | Cross-Cutting | AC1: a Calendar action exists in the catalogue and in the booking dialog", () => {
  assert.equal(
    (HTML.match(/openVenueCalendar\(/g) || []).length >= 2,
    true,
    "the catalogue and the booking dialog both offer it",
  );
  assert.match(HTML, /x-if="venueCalendar"/, "the calendar view is rendered");
  assert.match(HTML, /x-for="day in venueCalendar\.days"/, "each day is listed");
  assert.match(HTML, /x-for="block in day\.busy"/, "each busy block is listed");
  assert.match(HTML, /closeVenueCalendar\(\)/, "the view can be closed");
  assert.match(HTML, /venueCalendar\.error/, "the server's message has somewhere to appear");
});

test("TC-9615 | Workflow Variation | AC1: closing the calendar clears it, leaving any booking dialog alone", async () => {
  const state = app("Coordinator");
  state.bookingDialog = { event: { id: 3 }, bookings: [] };
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });
  assert.ok(state.venueCalendar);

  state.closeVenueCalendar();
  assert.equal(state.venueCalendar, null);
  assert.ok(state.bookingDialog, "the booking dialog underneath stays open");
});

test("TC-9616 | Boundary Path | AC1: a second open replaces the first, and a slow reply cannot overwrite it", async () => {
  const state = app();
  let finishFirst;
  state.apiRequest = () => new Promise((resolve) => (finishFirst = resolve));
  const firstOpen = state.openVenueCalendar(VENUE);
  assert.equal(state.venueCalendar.busy, true, "the view reports that it is loading");

  const second = { ...VENUE, id: 5, name: "Seminar Room" };
  state.apiRequest = async () => ({ ok: true, status: 200, data: calendarPayload({ venue_id: 5, name: "Seminar Room" }) });
  await state.openVenueCalendar(second);
  assert.equal(state.venueCalendar.venue.id, 5);

  finishFirst({ ok: true, status: 200, data: calendarPayload() });
  await firstOpen;
  assert.equal(state.venueCalendar.venue.id, 5, "the abandoned request does not replace the current view");
  assert.equal(state.venueCalendar.data.name, "Seminar Room");
});

test("TC-9617 | Cross-Cutting | AC1: a calendar opened from the booking dialog sits on top of it, and Escape closes only the calendar", () => {
  // Both views are modal backdrops, and the booking dialog comes later in the page, so without its own
  // layer the calendar opens hidden behind the dialog that opened it.
  const css = readFileSync(new URL("../src/components.css", import.meta.url), "utf8");
  const layer = selector => {
    const start = css.indexOf(`${selector} {`);
    assert.ok(start > -1, `${selector} has a rule`);
    return Number(css.slice(start, css.indexOf("}", start)).match(/z-index: (\d+)/)?.[1]);
  };
  assert.match(HTML, /x-if="venueCalendar"><div class="modal-backdrop modal-backdrop-stacked"/, "the calendar's backdrop has the stacked layer");
  assert.ok(layer(".modal-backdrop-stacked") > layer(".modal-backdrop"), "the stacked layer is above the dialog's");

  // One Escape closes the calendar (its own handler) and leaves the booking dialog open underneath.
  const handler = HTML.match(/x-if="bookingDialog"><div class="modal-backdrop" @keydown\.escape\.window="([^"]*)"/)?.[1];
  assert.ok(handler, "the booking dialog has an Escape handler");
  const pressEscape = state => new Function("scope", `with (scope) { ${handler} }`)(state);
  const state = app("Coordinator");
  let closed = 0;
  state.closeBookings = () => { closed += 1; };
  state.bookingDialog = { event: { id: 3 }, bookings: [] };
  state.venueCalendar = { venue: { id: 4, name: "Grand Hall" } };
  pressEscape(state);
  assert.equal(closed, 0, "Escape on the calendar does not also close the booking dialog");
  state.venueCalendar = null;
  pressEscape(state);
  assert.equal(closed, 1, "with no calendar open, Escape still closes the booking dialog");
});

test("TC-9618 | Cross-Cutting | AC4: signing out closes an open calendar, so the next person to sign in never sees it", async () => {
  const state = app("Coordinator");
  await openWith(state, { ok: true, status: 200, data: calendarPayload() });
  assert.ok(state.venueCalendar, "the calendar is open");
  // Logout and any 401 (for example from the 15-second refresh) both go through resetSignedOutState.
  resetSignedOutState(state);
  assert.equal(state.venueCalendar, null);
});
