// SCRUM-101: the catalogue refresh after a save or a retire was skipped when another
// refresh (the page's regular one) was already running, and the older reply then
// put the old list back on screen. The later refresh now runs once that one ends.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";
import * as venueRecord from "../src/venue-record.js";

const HALL = { id: 7, name: "Grand Hall", location: "Block A", capacity: 120, setup_time_mins: 30, turnaround_time_mins: 45, facilities: [], supported_layouts: [] };
const ROOM = { id: 8, name: "Small Room", location: "Block B", capacity: 20, setup_time_mins: 30, turnaround_time_mins: 45, facilities: [], supported_layouts: [] };

function app() {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], requestedFrontendPath: () => null, normalizeRoleName: normalizeRole,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} }, ...venueRecord,
  });
  const state = factory();
  state.user = { user_id: 3, role: "venue_staff" };
  return state;
}

// The catalogue as the backend would give it now. The first GET /venues can be held
// back, as an older reply that arrives late, by passing hold: true to refreshInFlight.
function backend(state, catalogue) {
  const server = { catalogue, venueLists: 0, held: null };
  state.apiRequest = async (path, options = {}) => {
    if (path === "/venues" && !options.method) {
      server.venueLists += 1;
      const reply = { ok: true, status: 200, data: [...server.catalogue] };
      if (server.holdNext) {
        server.holdNext = false;
        return new Promise(resolve => { server.held = () => resolve(reply); });
      }
      return reply;
    }
    if (path === "/venues/7/retire") {
      server.catalogue = server.catalogue.filter(venue => venue.id !== 7);
      return { ok: true, status: 200, data: { affected_bookings: [] } };
    }
    if (path === "/venues/7" && options.method === "PATCH") {
      server.catalogue = server.catalogue.map(venue => venue.id === 7 ? { ...venue, ...options.body } : venue);
      return { ok: true, status: 200, data: { venue: server.catalogue[0], flagged_bookings: [] } };
    }
    return { ok: true, status: 200, data: [] };
  };
  return server;
}

// Start a refresh and leave it waiting for its GET /venues reply.
async function refreshInFlight(state, server) {
  server.holdNext = true;
  return state.fetchVenueWorkspace();
}

test("TC-10160 retiring during a refresh still leaves the retired venue off the catalogue", async () => {
  const state = app();
  const server = backend(state, [HALL, ROOM]);
  await state.fetchVenueWorkspace();
  const refresh = refreshInFlight(state, server);
  state.askRetireVenue(HALL);
  await state.confirmRetireVenue();
  server.held();
  await refresh;
  assert.deepEqual(state.venueWorkspace.venues.map(venue => venue.name), ["Small Room"]);
  assert.equal(state.venueWorkspace.busy, false);
});

test("TC-10161 saving during a refresh still shows the saved value in the catalogue", async () => {
  const state = app();
  const server = backend(state, [HALL]);
  await state.fetchVenueWorkspace();
  state.venueWorkspace.editing = HALL;
  state.venueWorkspace.form = venueRecord.venueFormFrom(HALL);
  state.venueWorkspace.form.location = "Block C";
  // The save goes first; the page's regular refresh starts while it is on its way.
  const plainRequest = state.apiRequest;
  let finishPatch;
  state.apiRequest = (path, options = {}) => options.method === "PATCH"
    ? new Promise(resolve => { finishPatch = async () => resolve(await plainRequest(path, options)); })
    : plainRequest(path, options);
  const save = state.updateVenue();
  const refresh = refreshInFlight(state, server);
  await finishPatch();
  await save;
  server.held();
  await refresh;
  assert.equal(state.venueWorkspace.venues[0].location, "Block C");
});

test("TC-10162 with nothing running, a save refreshes the catalogue exactly once", async () => {
  const state = app();
  const server = backend(state, [HALL]);
  await state.fetchVenueWorkspace();
  const before = server.venueLists;
  state.askRetireVenue(HALL);
  await state.confirmRetireVenue();
  assert.equal(server.venueLists, before + 1);
  assert.equal(state.venueWorkspace.refreshAgain, false);
});

test("TC-10163 several refreshes asked for during one run again only once", async () => {
  const state = app();
  const server = backend(state, [HALL]);
  await state.fetchVenueWorkspace();
  const before = server.venueLists;
  const refresh = refreshInFlight(state, server);
  await state.fetchVenueWorkspace();
  await state.fetchVenueWorkspace();
  server.held();
  await refresh;
  assert.equal(server.venueLists, before + 2);
  assert.equal(state.venueWorkspace.refreshAgain, false);
});

test("TC-10164 signing out during a refresh runs nothing more", async () => {
  const state = app();
  const server = backend(state, [HALL]);
  await state.fetchVenueWorkspace();
  const before = server.venueLists;
  const refresh = refreshInFlight(state, server);
  await state.fetchVenueWorkspace();
  resetSignedOutState(state);
  server.held();
  await refresh;
  assert.equal(server.venueLists, before + 1);
});

test("TC-10165 a failed older refresh still leads to the later one", async () => {
  const state = app();
  const server = backend(state, [HALL]);
  await state.fetchVenueWorkspace();
  const plainRequest = state.apiRequest;
  let failList;
  state.apiRequest = (path, options = {}) => {
    state.apiRequest = plainRequest;
    return new Promise(resolve => { failList = () => resolve({ ok: false, status: 500, data: { detail: "Database unavailable" } }); });
  };
  const refresh = state.fetchVenueWorkspace();
  server.catalogue = [ROOM];
  await state.fetchVenueWorkspace();
  failList();
  await refresh;
  assert.deepEqual(state.venueWorkspace.venues.map(venue => venue.name), ["Small Room"]);
  assert.equal(state.venueWorkspace.busy, false);
});
