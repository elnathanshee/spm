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
    NAVIGATION: [],
    normalizeRoleName: normalizeRole,
    resolveRolePath,
    requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() },
    Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
  };
  vm.runInNewContext(
    source
      .trimStart()
      .replace(/^const API_BASE.*$/m, 'const API_BASE = "";')
      .replace(/^import[\s\S]*?from ".*?";/gm, ""),
    context
  );
  const state = factory();
  state.user = { user_id: 3, role: normalizeRole(role) };
  return state;
}

test("TC-10111 (F4) the Coordinator page counts only on its filters and section headings, and keeps finished requests apart", () => {
  const page = html.slice(html.indexOf("currentPath === '/coordinator/events'"), html.indexOf("      </main>"));
  assert.doesNotMatch(page, /summary-strip|queue-counters/, "no summary row repeating the filter and section counts");
  assert.match(page, /<div class="list-controls" x-data="\{ key: 'assigned' \}">/);
  assert.match(page, /<span class="group-count" x-text="group.events.length">/);
  assert.doesNotMatch(page, /In progress|Review queue|including decided ones/);
});

test("TC-10112 (F5) event detail leaves out the id, the status and the raw user ids, and keeps the rest in order", () => {
  const state = app("coordinator");
  const detail = { id: 1, title: "Gala", status: "Approved", organiser_id: 1, assigned_coordinator_id: 3, organiser: "organiser1", assigned_coordinator: "coord1", clarification_note: "TEST" };
  // clarification_note is now shown on its own, after the details (clarificationNote(), TC-9998)
  assert.deepEqual(plain(state.eventDetailEntries(detail)).map(([key]) => key), ["title", "organiser", "assigned_coordinator"]);
  assert.deepEqual(plain(state.eventDetailEntries(null)), []);
  assert.match(html, /x-for="\[key, value\] in eventDetailEntries\(eventDetail.detail\)"/);
});

// Changed on purpose (SCRUM-45): the placeholder is gone. The page now lists open events
// from GET /registrations/events, so it has buttons and a different empty state.
// F7's point stands: it is the Attendee's own screen, with no role note.
test("TC-10113 (F7) the Attendee page is its own screen with a plain empty state and no role note", () => {
  assert.equal(resolveRolePath("/attendee", "Attendee").unbuiltRole, false);
  const start = html.indexOf("currentPath === '/attendee' && user.role === 'Attendee'");
  const page = html.slice(start, html.indexOf("<!-- SCRUM-86", start));
  assert.match(page, /<h2>No events are open for registration<\/h2><p>Please check back later\.<\/p>/);
  assert.doesNotMatch(html, /Role-based access is active|Your focused workspace is ready|no attendee events/);
  assert.doesNotMatch(page, /list-controls/, "nothing to search or sort");
});

test("TC-10114 (F8) Venue Staff booking rows show when a hold expires or expired", () => {
  // The "Date and time" cell of the Venue Staff booking table; only Pending and Expired holds are shown.
  const row = html.slice(html.indexOf('<td data-label="Date and time" class="booking-cell-when cell-inline">'), html.indexOf('<td data-label="Status"', html.indexOf('booking-cell-when')));
  assert.match(row, /booking.hold_expires_at && \['Pending', 'Expired'\].includes\(booking.status\)/);
  assert.match(row, /booking.status === 'Expired' \? 'expired' : 'expires'/);
  assert.match(row, /formatHoldExpiry\(booking.hold_expires_at\)/);
});

test("TC-10115 (F9) the Venue Staff dialog is titled Review request only while the booking is Pending", () => {
  const title = html.match(/<h2 id="staff-booking-title"[^>]*>/)[0];
  assert.match(title, /x-text="staffBooking.detail && staffBooking.detail.status !== 'Pending' \? 'Booking details' : 'Review request'"/);
});
