// [SCRUM-45, 46, 47, 52] The Attendee's Event directory screen: open events,
// register, my registrations and withdraw with a confirm step. The component is
// tested directly with a stubbed apiRequest, and the markup is read as text.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { attendeeRegistration } from "../src/attendee-registration.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const mainJs = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const pageStart = html.indexOf("currentPath === '/attendee' && user.role === 'Attendee'");
const page = html.slice(pageStart, html.indexOf("<!-- [Sprint 5] SCRUM-86", pageStart));

function openEvent(overrides = {}) {
  return {
    event_id: 7,
    title: "Open Day",
    description: "Come along",
    proposed_start: "2099-03-10T14:00:00",
    venues: [{ name: "Main Hall", location: "Block A" }],
    places_left: 5,
    my_status: null,
    event_state: "open",
    ...overrides,
  };
}

// A screen whose backend answers from `server`, with every call recorded.
function screen(server = {}) {
  const state = attendeeRegistration();
  const calls = [];
  state.calls = calls;
  state.formatDate = value => (value ? `on ${value}` : "Not set");
  state.apiRequest = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET", options });
    if (path === "/registrations/events") return server.open || { ok: true, status: 200, data: [] };
    if (path === "/registrations/mine") return server.mine || { ok: true, status: 200, data: [] };
    return server.action || { ok: true, status: 200, data: { message: "Done." } };
  };
  return state;
}

function ok(data) {
  return { ok: true, status: 200, data };
}

// --- SCRUM-45 ---

test("TC-4520 opening the screen loads the open events and my registrations", async () => {
  const state = screen({ open: ok([openEvent()]), mine: ok([]) });
  await state.init();
  assert.deepEqual(state.calls.map(call => call.path).sort(), ["/registrations/events", "/registrations/mine"]);
  assert.equal(state.regLoaded, true);
  assert.equal(state.regEvents[0].title, "Open Day");
  assert.equal(state.regError, "");
});

test("TC-4521 a failed load shows a sentence the user can act on, and a retry recovers", async () => {
  const server = { open: { ok: false, status: 0, data: { detail: "Cannot reach the backend service." } } };
  const state = screen(server);
  await state.init();
  assert.equal(state.regLoaded, false);
  assert.equal(state.regError, "Cannot reach the backend service.");
  server.open = ok([openEvent()]);
  await state.loadRegistrationData();
  assert.equal(state.regError, "");
  assert.equal(state.regLoaded, true);
});

test("TC-4522 what an event shows: date, venue names and locations, and places left", () => {
  const state = screen();
  const event = openEvent({ venues: [{ name: "Main Hall", location: "Block A" }, { name: "Annex", location: "Block B" }] });
  assert.equal(state.regWhen(event), "on 2099-03-10T14:00:00");
  assert.equal(state.regVenues(event), "Main Hall, Block A; Annex, Block B");
  assert.equal(state.regVenues(openEvent({ venues: [] })), "Venue to be confirmed");
  assert.equal(state.regPlaces(openEvent({ places_left: 5 })), "5 places left");
  assert.equal(state.regPlaces(openEvent({ places_left: 1 })), "1 place left");
  assert.equal(state.regPlaces(openEvent({ places_left: 0 })), "Full");
});

test("TC-4523 the screen replaces the placeholder, keeps the role gate and names no internal field", () => {
  assert.ok(pageStart > 0, "the fixed role gate is still there");
  assert.match(page, /x-data="attendeeRegistration\(\)"/);
  assert.match(page, /<h1>Event directory<\/h1>/);
  assert.doesNotMatch(page, /No events are available yet/);
  assert.doesNotMatch(html, /no route lists events for this role/);
  for (const internal of ["purpose", "organiser", "coordinator", "requirements", "status_history", "accessibility", "registration_needs"]) {
    assert.doesNotMatch(page.toLowerCase(), new RegExp(internal), internal);
  }
  assert.match(mainJs, /import \{ attendeeRegistration \} from "\.\/attendee-registration\.js";/);
  assert.match(mainJs, /Alpine\.data\("attendeeRegistration", attendeeRegistration\);/);
});

test("TC-4524 the screen has empty states, a live status and an alert for errors", () => {
  assert.match(page, /No events are open for registration/);
  assert.match(page, /You haven't registered for any events/);
  assert.match(page, /x-text="regNotice"/);
  assert.match(page, /role="status"[^>]*x-text="regNotice"|x-text="regNotice"[^>]*role="status"/);
  assert.match(page, /role="alert"/);
  assert.match(page, /@click="loadRegistrationData\(\)"/);
});

// --- SCRUM-46 ---

test("TC-4620 registering posts to the event, says so and reloads the places", async () => {
  const server = { open: ok([openEvent()]), action: { ok: true, status: 201, data: { message: 'You are registered for "Open Day".' } } };
  const state = screen(server);
  await state.init();
  state.calls.length = 0;
  server.open = ok([openEvent({ places_left: 4, my_status: "registered" })]);
  await state.registerFor(state.regEvents[0]);
  assert.equal(state.calls[0].path, "/registrations/events/7");
  assert.equal(state.calls[0].method, "POST");
  assert.equal(state.calls[0].options.inlineErrors, true);
  assert.equal(state.regNotice, 'You are registered for "Open Day".');
  assert.equal(state.regEvents[0].places_left, 4);
  assert.equal(state.regEvents[0].my_status, "registered");
  assert.equal(state.regBusyId, null);
});

test("TC-4621 a refusal shows the backend's sentence and still refreshes the list", async () => {
  const server = { open: ok([openEvent({ places_left: 1 })]), action: { ok: false, status: 409, data: { detail: "This event is full. A place may open up if someone withdraws." } } };
  const state = screen(server);
  await state.init();
  server.open = ok([openEvent({ places_left: 0 })]);
  await state.registerFor(state.regEvents[0]);
  assert.equal(state.regError, "This event is full. A place may open up if someone withdraws.");
  assert.equal(state.regNotice, "");
  assert.equal(state.regEvents[0].places_left, 0);
  assert.equal(state.regBusyId, null);
});

test("TC-4622 only an event with a place that I don't already hold can be registered for", () => {
  const state = screen();
  assert.equal(state.regCanRegister(openEvent()), true);
  assert.equal(state.regCanRegister(openEvent({ my_status: "withdrawn" })), true);
  assert.equal(state.regCanRegister(openEvent({ places_left: 0 })), false);
  assert.equal(state.regCanRegister(openEvent({ my_status: "registered" })), false);
  assert.equal(state.regRegisterLabel(openEvent()), "Register");
  assert.equal(state.regRegisterLabel(openEvent({ my_status: "withdrawn" })), "Register again");
});

test("TC-4623 a second click while saving sends nothing", async () => {
  const state = screen({ open: ok([openEvent()]) });
  await state.init();
  state.calls.length = 0;
  state.regBusyId = 7;
  await state.registerFor(openEvent());
  assert.deepEqual(state.calls, []);
});

test("TC-4624 the register button is labelled for its event and disabled while saving", () => {
  assert.match(page, /@click="registerFor\(event\)"/);
  assert.match(page, /x-show="regCanRegister\(event\)"/);
  assert.match(page, /:disabled="regBusyId !== null"/);
  assert.match(page, /:aria-label="`\$\{regRegisterLabel\(event\)\} for \$\{event\.title\}`"/);
});

// --- SCRUM-47 ---

test("TC-4720 my status reads as a plain word with a matching tone", () => {
  const state = screen();
  const registered = openEvent({ my_status: "registered" });
  assert.equal(state.regStatusText(registered), "Registered");
  assert.equal(state.regStatusClass(registered), "badge-success");
  const withdrawn = openEvent({ my_status: "withdrawn" });
  assert.equal(state.regStatusText(withdrawn), "Withdrawn");
  assert.equal(state.regStatusClass(withdrawn), "badge-muted");
  const cancelled = openEvent({ my_status: "registered", event_state: "cancelled" });
  assert.equal(state.regStatusText(cancelled), "Event cancelled");
  assert.equal(state.regStatusClass(cancelled), "badge-danger");
  const completed = openEvent({ my_status: "registered", event_state: "completed" });
  assert.equal(state.regStatusText(completed), "Event completed");
  assert.equal(state.regStatusClass(completed), "badge-muted");
});

test("TC-4721 withdrawing needs a confirm step: asking sends nothing, keeping my place clears it", async () => {
  const state = screen({ mine: ok([openEvent({ my_status: "registered" })]) });
  await state.init();
  state.calls.length = 0;
  state.askWithdraw(state.regMine[0]);
  assert.equal(state.regConfirmId, 7);
  assert.deepEqual(state.calls, []);
  state.keepPlace();
  assert.equal(state.regConfirmId, null);
  assert.deepEqual(state.calls, []);
});

test("TC-4722 confirming withdraws, says so and reloads", async () => {
  const registered = openEvent({ my_status: "registered" });
  const server = { open: ok([registered]), mine: ok([registered]), action: ok({ message: 'You have withdrawn from "Open Day".' }) };
  const state = screen(server);
  await state.init();
  state.askWithdraw(state.regMine[0]);
  state.calls.length = 0;
  server.mine = ok([openEvent({ my_status: "withdrawn" })]);
  await state.confirmWithdraw(state.regMine[0]);
  assert.equal(state.calls[0].path, "/registrations/events/7/withdraw");
  assert.equal(state.calls[0].method, "POST");
  assert.equal(state.regNotice, 'You have withdrawn from "Open Day".');
  assert.equal(state.regMine[0].my_status, "withdrawn");
  assert.equal(state.regConfirmId, null);
});

test("TC-4723 a refused withdrawal shows why and closes the confirm step", async () => {
  const registered = openEvent({ my_status: "registered" });
  const state = screen({ mine: ok([registered]), action: { ok: false, status: 409, data: { detail: "This event is cancelled, so there is nothing to withdraw from." } } });
  await state.init();
  state.askWithdraw(registered);
  await state.confirmWithdraw(registered);
  assert.equal(state.regError, "This event is cancelled, so there is nothing to withdraw from.");
  assert.equal(state.regConfirmId, null);
});

test("TC-4724 only a live registration can be withdrawn", () => {
  const state = screen();
  assert.equal(state.regCanWithdraw(openEvent({ my_status: "registered" })), true);
  assert.equal(state.regCanWithdraw(openEvent({ my_status: "registered", event_state: "closed" })), true);
  assert.equal(state.regCanWithdraw(openEvent({ my_status: "withdrawn" })), false);
  assert.equal(state.regCanWithdraw(openEvent({ my_status: "registered", event_state: "cancelled" })), false);
  assert.equal(state.regCanWithdraw(openEvent({ my_status: "registered", event_state: "completed" })), false);
});

test("TC-4725 the markup has the confirm step, with focus moved into it and a clear way back", () => {
  assert.match(page, /@click="askWithdraw\(item\)"/);
  assert.match(page, /x-if="regConfirmId === item\.event_id"/);
  assert.match(page, /@click="confirmWithdraw\(item\)"/);
  assert.match(page, /@click="keepPlace\(\)"/);
  assert.match(page, /Keep my place/);
  assert.match(page, /x-init="\$nextTick\(\(\) => \$el\.focus\(\)\)"/);
  assert.match(page, /x-text="regStatusText\(item\)"/);
});

// --- SCRUM-52 ---

test("TC-5220 the three notices read as titled event updates for an Attendee, with no link to the internal event page", () => {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(
    source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""),
    {
      NAVIGATION: [],
      normalizeRoleName: role => role,
      requestedFrontendPath: () => null,
      document: { addEventListener: (_, fn) => fn() },
      Alpine: { data: (_, fn) => { factory = fn; } },
      window: { location: { hash: "" }, setInterval: () => {} },
    },
  );
  const state = factory();
  assert.equal(state.notificationTitle({ kind: "registered_event_cancelled" }), "Event cancelled");
  assert.equal(state.notificationTitle({ kind: "registered_event_changed" }), "Event changed");
  assert.equal(state.notificationTitle({ kind: "registered_details_changed" }), "Event details updated");
  state.user = { role: "Attendee" };
  assert.equal(state.notificationTarget({ id: 1, event_id: 7, booking_id: null, kind: "registered_event_changed" }), null);
});
