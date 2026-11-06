// [SCRUM-48, SCRUM-65] The registrations panel in the event detail dialog: the
// setting toggle and the list, for the Organiser and the assigned Coordinator.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { eventRegistrations } from "../src/event-registrations.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const mainJs = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const panelStart = html.indexOf('x-data="eventRegistrations()"');
const panel = html.slice(panelStart, html.indexOf("<!-- [Sprint 5] SCRUM-49", panelStart));

const SETTING = { event_id: 7, enabled: true, open: true, updated_by: "org_a", updated_at: "2099-01-01T00:00:00", message: "Registration is open for this event." };
const LIST = {
  event_id: 7,
  capacity: 3,
  registered_count: 1,
  places_left: 2,
  registrations: [
    { id: 1, attendee: "att_1", status: "registered", registered_at: "2099-01-01T00:00:00", withdrawn_at: null },
    { id: 2, attendee: "att_2", status: "withdrawn", registered_at: "2099-01-01T00:00:00", withdrawn_at: "2099-01-02T00:00:00" },
  ],
};

function ok(data) {
  return { ok: true, status: 200, data };
}

function panelFor(role, userId, detail, server = {}) {
  const state = eventRegistrations();
  const calls = [];
  state.calls = calls;
  state.user = { user_id: userId, role };
  state.eventDetail = { id: 7, detail };
  state.formatUtc = value => `at ${value}`;
  state.apiRequest = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET", options });
    if (options.method === "PUT") return server.put || ok({ ...SETTING, enabled: options.body.enabled, open: options.body.enabled });
    if (path.endsWith("/registration-settings")) return server.setting || ok(SETTING);
    return server.list || ok(LIST);
  };
  return state;
}

const DETAIL = { id: 7, organiser_id: 1, assigned_coordinator_id: 2 };

// --- SCRUM-48 ---

test("TC-4820 the panel is for the event's Organiser and assigned Coordinator only", () => {
  assert.equal(panelFor("Organiser", 1, DETAIL).canManageRegistrations(), true);
  assert.equal(panelFor("Coordinator", 2, DETAIL).canManageRegistrations(), true);
  assert.equal(panelFor("Organiser", 9, DETAIL).canManageRegistrations(), false);
  assert.equal(panelFor("Coordinator", 9, DETAIL).canManageRegistrations(), false);
  assert.equal(panelFor("Coordinator", 1, DETAIL).canManageRegistrations(), false, "an id match in the wrong role isn't enough");
  assert.equal(panelFor("Event Coordinator Lead", 1, DETAIL).canManageRegistrations(), false);
  assert.equal(panelFor("Coordinator", 2, { ...DETAIL, assigned_coordinator_id: null }).canManageRegistrations(), false);
});

test("TC-4821 someone else opening the dialog triggers no request", async () => {
  const state = panelFor("Coordinator", 9, DETAIL);
  await state.init();
  assert.deepEqual(state.calls, []);
  assert.equal(state.regLoaded, false);
});

test("TC-4822 opening loads the setting and the list for this event", async () => {
  const state = panelFor("Organiser", 1, DETAIL);
  await state.init();
  assert.deepEqual(state.calls.map(call => call.path).sort(), ["/events/7/registration-settings", "/events/7/registrations"]);
  assert.equal(state.calls[0].options.inlineErrors, true);
  assert.equal(state.regLoaded, true);
  assert.equal(state.regList.registrations.length, 2);
});

test("TC-4823 the counts read as a sentence, with or without a known capacity", () => {
  const state = panelFor("Organiser", 1, DETAIL);
  state.regList = LIST;
  assert.equal(state.regCounts(), "1 registered of 3 places, 2 left.");
  state.regList = { ...LIST, registered_count: 3, places_left: 0 };
  assert.equal(state.regCounts(), "3 registered of 3 places, 0 left.");
  state.regList = { ...LIST, capacity: null, registered_count: 0, places_left: 0 };
  assert.equal(state.regCounts(), "The number of places isn't known yet: it needs an expected attendance or a Confirmed venue.");
});

test("TC-4824 a row shows the Attendee, a plain status and the right time", () => {
  const state = panelFor("Organiser", 1, DETAIL);
  assert.equal(state.regRowStatus(LIST.registrations[0]), "Registered");
  assert.equal(state.regRowClass(LIST.registrations[0]), "badge-success");
  assert.equal(state.regRowStatus(LIST.registrations[1]), "Withdrawn");
  assert.equal(state.regRowClass(LIST.registrations[1]), "badge-muted");
  assert.equal(state.regRowTime(LIST.registrations[0]), "at 2099-01-01T00:00:00");
  assert.equal(state.regRowTime(LIST.registrations[1]), "at 2099-01-02T00:00:00");
});

test("TC-4825 a failed load shows why, and a retry recovers", async () => {
  const server = { list: { ok: false, status: 500, data: { detail: "Something went wrong." } } };
  const state = panelFor("Organiser", 1, DETAIL, server);
  await state.init();
  assert.equal(state.regError, "Something went wrong.");
  assert.equal(state.regLoaded, false);
  server.list = ok(LIST);
  await state.loadEventRegistrations();
  assert.equal(state.regError, "");
  assert.equal(state.regLoaded, true);
});

test("TC-4827 the panel reads again when the event's status changes in the dialog, and not once the dialog is closing", async () => {
  // [SCRUM-48] The lifecycle panel changes the status in this same dialog, and a cancel also releases the venue.
  assert.match(panel, /x-init="\$watch\('eventDetail\?\.detail\?\.status', \(\) => refreshRegistrations\(\)\)"/);
  const server = {};
  const state = panelFor("Organiser", 1, DETAIL, server);
  await state.init();
  assert.equal(state.regSetting.message, "Registration is open for this event.");
  server.setting = ok({ ...SETTING, open: false, message: "This event is cancelled, so registration stays closed." });
  server.list = ok({ ...LIST, capacity: 1, places_left: 0 });
  await state.refreshRegistrations();
  assert.equal(state.regSetting.message, "This event is cancelled, so registration stays closed.");
  assert.equal(state.regList.capacity, 1);
  const before = state.calls.length;
  state.eventDetail = null;
  await state.refreshRegistrations();
  assert.equal(state.calls.length, before, "a closing dialog asks for nothing");
});

test("TC-4826 the markup sits at its fixed place with a gate, a table and an empty state", () => {
  assert.ok(panelStart > 0);
  const place = html.indexOf("<!-- [Sprint 5] SCRUM-48 registrations panel");
  assert.ok(place > 0 && place < panelStart, "the panel is directly below the group's comment line");
  assert.ok(panelStart < html.indexOf("<!-- [Sprint 5] SCRUM-49 change requests panel"));
  assert.match(panel, /x-show="canManageRegistrations\(\)"/);
  assert.match(panel, /x-for="row in regList\.registrations"/);
  assert.match(panel, /x-text="row\.attendee"/);
  assert.match(panel, /data-label="Attendee"/);
  assert.match(panel, /Nobody has registered yet/);
  assert.match(panel, /role="alert"/);
  assert.match(mainJs, /import \{ eventRegistrations \} from "\.\/event-registrations\.js";/);
  assert.match(mainJs, /Alpine\.data\("eventRegistrations", eventRegistrations\);/);
});

// --- SCRUM-65 ---

test("TC-6560 the toggle sends the opposite of the current setting and shows the new state", async () => {
  const state = panelFor("Coordinator", 2, DETAIL);
  await state.init();
  state.calls.length = 0;
  await state.toggleRegistration();
  assert.equal(state.calls[0].path, "/events/7/registration-settings");
  assert.equal(state.calls[0].method, "PUT");
  assert.deepEqual(state.calls[0].options.body, { enabled: false });
  assert.equal(state.regSetting.enabled, false);
  assert.equal(state.regBusy, false);
  await state.toggleRegistration();
  assert.deepEqual(state.calls[1].options.body, { enabled: true });
  assert.equal(state.regSetting.enabled, true);
});

test("TC-6561 a failed save keeps the old setting and shows why", async () => {
  const state = panelFor("Organiser", 1, DETAIL, { put: { ok: false, status: 409, data: { detail: "Someone else changed this setting at the same time. Please try again." } } });
  await state.init();
  await state.toggleRegistration();
  assert.equal(state.regSetting.enabled, true);
  assert.equal(state.regError, "Someone else changed this setting at the same time. Please try again.");
  assert.equal(state.regBusy, false);
});

test("TC-6562 the button says what it will do, and the backend's sentence explains the state", async () => {
  const state = panelFor("Organiser", 1, DETAIL);
  await state.init();
  assert.equal(state.regToggleLabel(), "Turn registration off");
  state.regSetting = { ...SETTING, enabled: false };
  assert.equal(state.regToggleLabel(), "Turn registration on");
  assert.match(panel, /@click="toggleRegistration\(\)"/);
  assert.match(panel, /:aria-pressed="regSetting\.enabled \? 'true' : 'false'"/);
  assert.match(panel, /:disabled="regBusy"/);
  assert.match(panel, /x-text="regSetting\.message"/);
});

test("TC-6563 a second click while saving sends nothing", async () => {
  const state = panelFor("Organiser", 1, DETAIL);
  await state.init();
  state.calls.length = 0;
  state.regBusy = true;
  await state.toggleRegistration();
  assert.deepEqual(state.calls, []);
});
