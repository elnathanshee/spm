import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole } from "../src/rbac.js";
import * as venueRecord from "../src/venue-record.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

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

// Naive UTC, as booking_detail_dict returns them
const at = hours => new Date(Date.now() + hours * 3600e3).toISOString().slice(0, 19);
const booking = (id, status, startHours, extra = {}) => ({
  id, booking_id: id, venue_id: 1, event_title: `Event ${id}`, requester_name: "coord1", status,
  booking_start: at(startHours), booking_end: at(startHours + 2), hold_expires_at: null, conflict_flag: null, rejection_reason: null, ...extra,
});

function workspace(state, bookings, { venues = [{ id: 1, name: "Grand Hall" }, { id: 2, name: "Room B" }] } = {}) {
  state.apiRequest = async path => path === "/venues" ? { ok: true, status: 200, data: venues }
    : { ok: true, status: 200, data: bookings.filter(item => path === `/venues/${item.venue_id}/bookings`) };
  return state.fetchVenueWorkspace();
}

test("TC-9971 bookings split into Needs a decision, Upcoming confirmed, then Past and closed", async () => {
  const state = app();
  await workspace(state, [
    booking(1, "Confirmed", 48), booking(2, "Pending", 24), booking(3, "Rejected", 72, { rejection_reason: "Clash" }),
    booking(4, "Confirmed", -48), booking(5, "Expired", 10), booking(6, "Pending", -30),
  ]);
  const groups = state.venueBookingGroups();
  assert.deepEqual(plain(groups.map(group => group.title)), ["Needs a decision", "Upcoming confirmed", "Past and closed"]);
  assert.deepEqual(plain(groups.map(group => group.bookings.map(item => item.id))), [[6, 2], [1], [4, 5, 3]], "Pending is decided whatever its date; ended Confirmed is the record");
  // The chip and the search apply first; empty sections aren't shown
  state.venueWorkspace.bookingFilter = "Confirmed";
  assert.deepEqual(plain(state.venueBookingGroups().map(group => [group.key, group.bookings.map(item => item.id)])), [["upcoming", [1]], ["closed", [4]]]);
  state.venueWorkspace.bookingFilter = "All";
  state.bookingView.query = "#2";
  assert.deepEqual(plain(state.venueBookingGroups().map(group => group.key)), ["decide"]);
});

test("TC-9972 Approve on the row confirms, reloads and announces; a 409 stays on the row", async () => {
  const state = app();
  const pending = booking(2, "Pending", 24, { venue_name: "Grand Hall" });
  await workspace(state, [pending]);
  const sent = [];
  let status = "Pending";
  state.apiRequest = async (path, options = {}) => {
    sent.push([options.method || "GET", path]);
    if (path.endsWith("/approve")) { status = "Confirmed"; return { ok: true, status: 200, data: { booking: { ...pending, status } } }; }
    return path === "/venues" ? { ok: true, status: 200, data: [{ id: 1, name: "Grand Hall" }] } : { ok: true, status: 200, data: [{ ...pending, status }] };
  };
  await state.approveBookingRow(state.venueWorkspace.bookings[0]);
  assert.deepEqual(sent.slice(0, 2), [["POST", "/venues/bookings/2/approve"], ["GET", "/venues"]]);
  assert.equal(state.venueWorkspace.bookings[0].status, "Confirmed");
  assert.equal(state.alert.message, "Event 2 confirmed at Grand Hall.");
  assert.deepEqual(plain(state.bookingRowState(pending)), {});

  // Refused: the message is shown on the row and the list reloads for the current status
  const again = app();
  await workspace(again, [booking(7, "Pending", 24)]);
  const calls = [];
  again.apiRequest = async (path, options = {}) => {
    calls.push(path);
    if (path.endsWith("/approve")) return { ok: false, status: 409, data: { detail: "Cannot approve: overlaps booking #3" } };
    return path === "/venues" ? { ok: true, status: 200, data: [{ id: 1, name: "Grand Hall" }] } : { ok: true, status: 200, data: [booking(7, "Pending", 24)] };
  };
  await again.approveBookingRow(again.venueWorkspace.bookings[0]);
  assert.deepEqual(plain(again.bookingRowState({ id: 7 })), { busy: false, error: "Cannot approve: overlaps booking #3" });
  assert.deepEqual(calls, ["/venues/bookings/7/approve", "/venues", "/venues/1/bookings"]);
  // Only Pending rows can be approved
  calls.length = 0;
  await again.approveBookingRow(booking(8, "Confirmed", 24));
  assert.deepEqual(calls, []);
});

test("TC-9973 Reject opens the review with the reason focused; the row has one filled button", async () => {
  const state = app();
  state.apiRequest = async () => ({ ok: true, status: 200, data: { id: 4, status: "Pending" } });
  await state.rejectBookingRow({ id: 4 });
  assert.equal(state.staffBooking.focusReason, true);
  await state.openStaffBooking(4);
  assert.equal(state.staffBooking.focusReason, false);
  assert.match(html, /<textarea id="staff-rejection-reason" x-init="staffBooking.focusReason && \$nextTick\(\(\) => \$el.focus\(\)\)"/);
  const actions = html.slice(html.indexOf('<td data-label="Actions" class="cell-end"><template x-if="booking.status === \'Pending\'">'));
  const cell = actions.slice(0, actions.indexOf("</td>"));
  assert.equal((cell.match(/button-primary/g) || []).length, 1, "Approve is the only filled button");
  assert.match(cell, /@click="rejectBookingRow\(booking\)"[^>]*>Reject<\/button>/);
  assert.doesNotMatch(html, /Review request<\/button>|'Review request' : 'View details'/, "rows open through the event link, not a separate button");
  assert.match(html, /<a class="row-link" :href="`#booking-\$\{booking.id\}`" @click.prevent="openStaffBooking\(booking.id\)" x-text="booking.event_title"><\/a>/);
});

test("TC-9974 the booking table aligns columns and shows status, date range, hold and clash", () => {
  const table = html.slice(html.indexOf('<table class="data-table venue-booking-table">'), html.indexOf("</table>", html.indexOf("venue-booking-table")));
  assert.match(table, /<th scope="col">Booking<\/th><th scope="col">Date and time<\/th><th scope="col">Status<\/th><th scope="col" class="cell-end">Actions<\/th>/);
  for (const label of ["Booking", "Date and time", "Status", "Actions"]) assert.match(table, new RegExp(`<td data-label="${label}"`));
  assert.match(table, /formatSchedule\(booking.booking_start, booking.booking_end\)/);
  assert.match(table, /bookingStatusClass\(booking.status\)/);
  assert.match(table, /<span class="row-flag">Possible clash<\/span> <span x-text="booking.conflict_flag.reason"><\/span>/);
  assert.doesNotMatch(table, /capacity|attendance/i, "the booking list has no attendance to compare (BACKEND_ISSUES #6)");
  assert.match(html, /class="booking-group table-container"/);
  assert.doesNotMatch(css, /\.booking-row\{|\.booking-table\{/, "the old grid rows are gone");
});

test("TC-9975 retiring lists the bookings that hold the venue, from the loaded list", async () => {
  const state = app();
  await workspace(state, [
    booking(1, "Pending", 24), booking(2, "Confirmed", 5), booking(3, "Confirmed", -48), booking(4, "Rejected", 24),
    booking(5, "Expired", 24), booking(6, "Pending", 24, { venue_id: 2 }),
  ]);
  const hall = state.venueWorkspace.venues[0];
  assert.deepEqual(plain(state.retireImpact(hall).map(item => item.id)), [2, 1]);
  assert.equal(state.retireImpactKnown(hall), true);
  const notice = html.slice(html.indexOf('aria-labelledby="venue-retire-title"'), html.indexOf("Retiring…"));
  assert.match(notice, /there's no way to bring a retired venue back in the app/);
  assert.match(notice, /They won't be cancelled or notified, and they'll leave Booking requests/);
  assert.match(notice, /@click="confirmRetireVenue\(\)"/, "nothing is retired until Retire venue is pressed");
  // A venue whose bookings didn't load says so instead of claiming there are none
  const partial = app();
  partial.apiRequest = async path => path === "/venues" ? { ok: true, status: 200, data: [{ id: 1, name: "Grand Hall" }] } : { ok: false, status: 500, data: { detail: "boom" } };
  await partial.fetchVenueWorkspace();
  assert.equal(partial.retireImpactKnown({ id: 1 }), false);
  assert.equal(partial.venueWorkspace.bookingError, true);
});

test("TC-9976 loading, error, retry and permission-denied states come from the venue list", async () => {
  const state = app();
  state.apiRequest = async (path, options) => { assert.equal(options.inlineErrors, true); return { ok: false, status: 500, data: { detail: "Database unavailable" } }; };
  await state.fetchVenueWorkspace();
  assert.deepEqual([state.venueWorkspace.loaded, state.venueWorkspace.error, state.venueWorkspace.denied], [false, "Database unavailable", false]);
  await workspace(state, []);
  assert.deepEqual([state.venueWorkspace.loaded, state.venueWorkspace.error], [true, ""]);
  // A failed refresh keeps the list shown
  state.apiRequest = async () => ({ ok: false, status: 503, data: { detail: "Try later" } });
  await state.fetchVenueWorkspace();
  assert.deepEqual([state.venueWorkspace.loaded, state.venueWorkspace.error], [true, "Try later"]);
  state.apiRequest = async () => ({ ok: false, status: 403, data: { detail: "Venue Staff access required" } });
  await state.fetchVenueWorkspace();
  assert.equal(state.venueWorkspace.denied, true);
  assert.match(html, /x-show="venueWorkspace.denied" role="alert"><div class="state-icon danger">!<\/div><h2>You can't manage venues<\/h2>/);
  assert.match(html, /Couldn't load venues and bookings: \$\{venueWorkspace.error\}/);
  assert.match(html, /Loading venues and bookings…/);
  assert.match(html, /<template x-if="venueWorkspace.loaded && !venueWorkspace.denied"><div class="venue-workspace">/);
  assert.doesNotMatch(html, /Active venues<small>|Unread updates<small>/, "no summary card repeats the catalogue total or the bell badge");
});
