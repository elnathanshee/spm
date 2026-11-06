// SCRUM-100: Venue Staff see bookings at retired venues and can correct one.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { retiredVenues } from "../src/retired-venues.js";

const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const MAIN = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");

const OLD_STORE = { id: 3, name: "Old Store", location: "Block Z", capacity: 20, is_active: false };
const BOOKING = { id: 11, booking_id: 11, event_title: "Annual Symposium", status: "Confirmed", booking_start: "2099-06-01T10:00:00", booking_end: "2099-06-01T12:00:00" };

function panel(routes) {
  const state = retiredVenues();
  state.calls = [];
  state.apiRequest = async (path, options) => {
    state.calls.push({ path, options });
    return routes[path] || { ok: false, status: 404, data: {} };
  };
  return state;
}

test("TC-10034 the panel lists retired venues with their bookings", async () => {
  const state = panel({
    "/venues?retired=true": { ok: true, status: 200, data: [OLD_STORE] },
    "/venues/3/bookings": { ok: true, status: 200, data: [BOOKING] },
  });
  await state.load();
  assert.equal(state.calls[0].options.inlineErrors, true);
  assert.deepEqual(state.venues.map(venue => venue.name), ["Old Store"]);
  assert.deepEqual(state.bookingsFor(OLD_STORE), [BOOKING]);
  assert.equal(state.loading, false);
  assert.equal(state.error, "");
});

test("TC-10035 no retired venues needs no booking requests", async () => {
  const state = panel({ "/venues?retired=true": { ok: true, status: 200, data: [] } });
  await state.load();
  assert.deepEqual(state.venues, []);
  assert.equal(state.calls.length, 1);
});

test("TC-10036 a failed list says so and keeps what was shown; a failed venue is named", async () => {
  const failing = panel({ "/venues?retired=true": { ok: false, status: 500, data: {} } });
  failing.venues = [OLD_STORE];
  await failing.load();
  assert.match(failing.error, /could not be loaded/i);
  assert.deepEqual(failing.venues, [OLD_STORE]);

  const partial = panel({
    "/venues?retired=true": { ok: true, status: 200, data: [OLD_STORE] },
    "/venues/3/bookings": { ok: false, status: 500, data: {} },
  });
  await partial.load();
  assert.deepEqual(partial.bookingsFor(OLD_STORE), []);
  assert.equal(partial.bookingsFailed(OLD_STORE), true);
});

test("TC-10037 the panel is in the Venue Staff workspace, offers a correction and reloads after a save or a retire", () => {
  const start = HTML.indexOf('x-data="retiredVenues()"');
  assert.ok(start > HTML.indexOf('id="venue-list-title"'), "after the catalogue");
  assert.ok(start < HTML.indexOf('currentPath === \'/attendee\''), "before the next workspace");
  const section = HTML.slice(start, start + 2500);
  assert.match(section, /\$watch\('venueWorkspace\.outcome', \(\) => load\(\)\)/);
  assert.match(section, /@click="correct\(venue\)"/);
  assert.match(section, /@click\.prevent="openStaffBooking\(booking\.id\)"/);
  assert.match(section, /aria-live="polite"/);
  assert.match(MAIN, /import \{ retiredVenues \} from "\.\/retired-venues\.js";/);
  assert.match(MAIN, /Alpine\.data\("retiredVenues", retiredVenues\);/);
});

test("TC-10038 Correct opens the edit form and moves focus to it, so the press has a visible result", async t => {
  const state = panel({});
  const input = { focused: 0, focus() { this.focused += 1; } };
  // All test files share one process, so the stub must not outlive this test.
  t.after(() => { delete globalThis.document; });
  globalThis.document = { getElementById: id => (id === "venue-name" ? input : null) };
  state.$nextTick = callback => callback();
  state.venueWorkspace = { editing: null };
  state.openVenueEdit = async venue => { state.venueWorkspace.editing = venue; };
  await state.correct(OLD_STORE);
  assert.equal(input.focused, 1);

  // The venue could not be opened: the form isn't showing, so focus stays put.
  state.openVenueEdit = async () => { state.venueWorkspace.editing = null; };
  await state.correct(OLD_STORE);
  assert.equal(input.focused, 1);
});

test("TC-10039 the Correct button uses it, and the panel reloads when the booking dialog closes", () => {
  const section = HTML.slice(HTML.indexOf('x-data="retiredVenues()"'), HTML.indexOf('x-data="retiredVenues()"') + 2500);
  assert.match(section, /@click="correct\(venue\)"/);
  assert.match(section, /\$watch\('staffBooking', value => \{ if \(!value\) load\(\); \}\)/);
});
