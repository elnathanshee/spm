import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

async function dialog() {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 2, username: "coord1", role: "Coordinator" };
  const event = { id: 7, title: "Gala", status: "Approved" };
  state.assignedEvents = [event];
  state.apiRequest = async path => path === "/venues" ? { ok: true, data: [{ id: 1, name: "Grand Hall" }, { id: 2, name: "Room B" }] } : { ok: true, data: [] };
  await state.openBookings(event);
  const sent = [];
  state.apiRequest = async (path, options = {}) => { sent.push([options.method || "GET", path, options.body]); return state.respond ? state.respond(path, options) : { ok: true, status: 200, data: [] }; };
  return { state, sent, form: state.bookingDialog };
}

test("TC-9961 Search venues sends nothing without a search field, and checks the period before calling", async () => {
  const { state, sent, form } = await dialog();
  await state.searchVenues();
  assert.equal(sent.length, 0);
  assert.equal(form.searchErrors.form, "Enter at least one search field.");
  form.search.available_only = true;
  await state.searchVenues();
  assert.equal(sent.length, 0, "Available only isn't a search field on its own");
  assert.equal(form.searchErrors.available_only, "Add a start and end time to show only available venues");
  form.search.start_time = "2027-01-01T10:00";
  form.search.end_time = "2027-01-01T09:00";
  await state.searchVenues();
  assert.equal(form.searchErrors.end_time, "End must be after start");
  form.search.end_time = "";
  await state.searchVenues();
  assert.equal(form.searchErrors.end_time, "Add an end time, or clear the start time");
  form.search = { ...form.search, start_time: "", available_only: false, min_capacity: 12.5 };
  await state.searchVenues();
  assert.equal(form.searchErrors.min_capacity, "Enter a whole number of guests, 1 or more");
  assert.equal(sent.length, 0, "no invalid search reached the API");
  form.search.min_capacity = 100;
  await state.searchVenues();
  assert.deepEqual(sent.map(([, path]) => path), ["/venues/search?min_capacity=100"]);
  assert.deepEqual(plain(form.searchErrors), {});
  assert.equal(form.searchMessage, "No venues found matching your criteria. Try fewer filters.");
});

test("TC-9962 Check suitability needs facilities, or 'No facilities required', which sends an empty list", async () => {
  const { state, sent, form } = await dialog();
  await state.checkSuitability(form.search.facilities);
  assert.equal(sent.length, 0);
  assert.equal(form.searchErrors.facilities, "Enter the facilities to check, or tick “No facilities required”");
  form.noFacilitiesRequired = true;
  state.respond = () => ({ ok: true, status: 200, data: { expected_attendance: null, venues: [{ id: 1, name: "Grand Hall", suitability: "unknown", checks: [] }] } });
  await state.checkSuitability(form.search.facilities);
  assert.deepEqual(sent.map(([, path]) => path), ["/events/7/venue-suitability?facilities="]);
  assert.equal(form.suitabilityMessage, "This event has no expected attendance, so capacity can't be checked.");
  assert.equal(form.resultsKind, "suitability");
  form.noFacilitiesRequired = false;
  await state.checkSuitability("Projector, Audio");
  assert.equal(sent.at(-1)[1], "/events/7/venue-suitability?facilities=Projector%2C%20Audio");
});

test("TC-9963 Submit booking request requires a venue and a valid period before calling", async () => {
  const { state, sent, form } = await dialog();
  await state.submitBooking();
  assert.equal(sent.length, 0);
  assert.deepEqual(plain(state.fieldErrors), { venue_id: "Select a venue", booking_start: "Enter a start date and time", booking_end: "Enter an end date and time" });
  Object.assign(form.form, { venue_id: "1", booking_start: "2099-01-01T10:00", booking_end: "2099-01-01T09:00" });
  await state.submitBooking();
  assert.equal(state.fieldErrors.booking_end, "End must be after start");
  assert.equal(sent.length, 0);
  form.form.booking_end = "2099-01-01T12:00";
  state.respond = path => path.endsWith("/bookings") && !path.startsWith("/events") ? { ok: true, status: 201, data: {} } : { ok: true, status: 200, data: [{ id: 9, status: "Pending" }] };
  await state.submitBooking();
  assert.equal(sent[0][0], "POST");
  assert.equal(sent[0][1], "/venues/1/bookings");
  assert.equal(form.requestMessage, "Request sent for Grand Hall. It's in Booking history as Pending until Venue Staff decide.");
});

test("TC-9964 a server refusal shows its message, without Pydantic's prefix", async () => {
  const { state, form } = await dialog();
  Object.assign(form.form, { venue_id: "1", booking_start: "2099-01-01T10:00", booking_end: "2099-01-01T12:00" });
  state.respond = () => ({ ok: false, status: 422, data: { detail: [{ loc: ["body"], msg: "Value error, booking_start cannot be in the past" }] } });
  await state.submitBooking();
  assert.equal(form.error, "booking_start cannot be in the past");
  state.respond = () => ({ ok: false, status: 409, data: { detail: "Venue 'Grand Hall' is unavailable for the requested time slot" } });
  await state.submitBooking();
  assert.equal(form.error, "Venue 'Grand Hall' is unavailable for the requested time slot");
});

test("TC-9965 'Request this venue' carries the venue and searched times into the request form, for available venues only", async () => {
  const { state, sent, form } = await dialog();
  form.search.start_time = "2099-03-01T09:00";
  form.search.end_time = "2099-03-01T17:00";
  state.selectVenueFromSearch({ id: 2, name: "Room B", is_available: false });
  assert.equal(form.form.venue_id, "", "an unavailable venue can't be requested from the results");
  state.selectVenueFromSearch({ id: 3, name: "New Hall", is_available: true });
  assert.deepEqual(plain(form.form), { venue_id: "3", booking_start: "2099-03-01T09:00", booking_end: "2099-03-01T17:00" });
  assert.ok(form.venues.some(venue => venue.id === 3), "added to the venue choice");
  assert.equal(sent.length, 0, "nothing is submitted until Submit booking request");
  assert.match(html, /<template x-if="canRequestFromSearch\(venue\)"><button type="button" class="button button-secondary button-compact" @click="selectVenueFromSearch\(venue\)"[^>]*>Request this venue<\/button><\/template>/);
  assert.match(html, /x-text="bookingDialog\.bookings\.length \? 'Request another venue' : 'Request a venue'"/);
  // Order: history, find and assess, request
  const order = ["booking-history-title", "find-venues-title", "request-venue-title"].map(id => html.indexOf(`id="${id}"`));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
});
