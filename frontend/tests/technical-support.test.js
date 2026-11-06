import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app(role = "technical support") {
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
  state.user = { user_id: 7, role: normalizeRole(role) };
  return state;
}

const lines = [
  { id: 1, event_id: 4, event_title: "AI Workshop", event_proposed_start: "2027-12-05T14:00:00", equipment_type: "Projector", quantity: 2, technical_requirements: "HDMI", requested_by: "coord1", created_at: "2026-11-04T03:00:00" },
  { id: 2, event_id: 4, event_title: "AI Workshop", event_proposed_start: "2027-12-05T14:00:00", equipment_type: "Microphone", quantity: 4, technical_requirements: null, requested_by: "coord1", created_at: "2026-11-04T03:05:00" },
  { id: 3, event_id: 1, event_title: "Gala", event_proposed_start: null, equipment_type: "Stage lights", quantity: 6, technical_requirements: null, requested_by: "lead1", created_at: "2026-11-04T03:10:00" },
];

test("TC-10101 (F1) the app component starts once: Alpine calls init() itself, so the root has no x-init", () => {
  const root = html.match(/<div x-data="appState\(\)"[^>]*>/)[0];
  assert.doesNotMatch(root, /x-init/);
  assert.match(source, /async init\(\)/);
});

test("TC-10102 (F2) notification times are read as UTC, like booking times", () => {
  const state = app("organiser");
  const previousTZ = process.env.TZ;
  try {
    process.env.TZ = "Asia/Singapore";
    assert.equal(state.formatNotificationTime("2026-11-04T03:55:11.861194"), state.formatNotificationTime("2026-11-04T03:55:11.861Z"));
    assert.match(state.formatNotificationTime("2026-11-04T03:55:11.861194"), /11:55/);
    assert.equal(state.formatNotificationTime(null), "Recently");
    assert.equal(state.formatNotificationTime("not a date"), "Recently");
  } finally {
    process.env.TZ = previousTZ;
  }
});

test("TC-10103 (F6) /technical is a real screen for Technical Support and /equipment-requests is proxied", () => {
  for (const role of ["technical support", "technical_support"]) {
    assert.deepEqual(plain(resolveRolePath("/technical", normalizeRole(role))), { accessDenied: false, currentPath: "/technical", unbuiltRole: false });
  }
  assert.equal(resolveRolePath("/attendee", "Attendee").unbuiltRole, false, "no placeholder pages are left");
  assert.match(readFileSync(new URL("../vite.config.js", import.meta.url), "utf8"), /"\/equipment-requests": backendProxy/);
  assert.match(html, /x-if="currentPath === '\/technical' && user.role === 'Technical Support' && technicalWorklist"/);
});

test("TC-10104 (F6) the worklist loads GET /equipment-requests and groups lines by event in backend order", async () => {
  const state = app();
  const paths = [];
  state.apiRequest = async (path, options) => { paths.push([path, options?.inlineErrors]); return { ok: true, status: 200, data: { equipment_requests: lines } }; };
  state.navigate("/technical");
  await new Promise(resolve => setTimeout(resolve));
  assert.deepEqual(paths, [["/equipment-requests", true]]);
  assert.equal(state.technicalWorklist.loaded, true);
  assert.equal(state.technicalWorklist.error, "");
  assert.deepEqual(plain(state.technicalEventGroups().map(group => [group.event_id, group.items.map(item => item.id)])), [[4, [1, 2]], [1, [3]]]);
});

test("TC-10105 (F6) a failed load shows the backend's message and keeps no data", async () => {
  const state = app();
  state.apiRequest = async () => ({ ok: false, status: 403, data: { detail: "Only Coordinators, Leads and Technical Support Staff can view equipment requirements" } });
  await state.fetchTechnicalWorklist();
  assert.equal(state.technicalWorklist.loaded, false);
  assert.deepEqual(plain(state.technicalWorklist.items), []);
  assert.match(state.technicalWorklist.error, /Technical Support Staff/);

  const html200 = app();
  html200.apiRequest = async () => ({ ok: true, status: 200, data: {} });
  await html200.fetchTechnicalWorklist();
  assert.equal(html200.technicalWorklist.loaded, false);
  assert.match(html200.technicalWorklist.error, /unexpected response/);
});

test("TC-10106 (F6) other roles never request the worklist, and signing out clears it", async () => {
  for (const role of ["organiser", "coordinator", "lead", "venue_staff", "attendee"]) {
    const state = app(role);
    let called = false;
    state.apiRequest = async () => { called = true; return { ok: true, data: { equipment_requests: [] } }; };
    await state.fetchTechnicalWorklist();
    assert.equal(called, false, role);
    assert.equal(state.technicalWorklist, null, role);
  }
  const state = app();
  state.technicalWorklist = { items: lines, busy: false, loaded: true, error: "" };
  resetSignedOutState(state);
  assert.equal(state.technicalWorklist, null);
});

// ---- Redesign: sections by event date, aligned tables, page states (UI_DECISIONS "Technical Support")
// Singapore wall time, as proposed_start is stored
const sgt = days => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
  .format(new Date(Date.now() + days * 86400e3)).replace(" ", "T");
const line = (id, eventId, days, extra = {}) => ({ id, event_id: eventId, event_title: `Event ${eventId}`, event_proposed_start: days === null ? null : sgt(days), equipment_type: `Item ${id}`, quantity: id, technical_requirements: null, requested_by: "coord1", created_at: `2026-11-0${id}T03:00:00`, ...extra });

test("TC-9991 events are split by date: the next 7 days first, then later, undated, and past", () => {
  const state = app();
  state.technicalWorklist = { loaded: true, items: [line(1, 10, 2), line(2, 10, 2), line(3, 11, 30), line(4, 12, null), line(5, 13, -3), line(6, 14, 0)] };
  const shape = () => plain(state.technicalSections().map(section => [section.key, section.events.map(event => event.event_id)]));
  assert.deepEqual(shape(), [["soon", [10, 14]], ["later", [11]], ["undated", [12]], ["past", [13]]]);
  assert.deepEqual(plain(state.technicalSections().map(section => section.label)), ["Today and the next 7 days", "Later", "Date not set", "Past events"]);
  assert.equal(state.technicalSections()[0].attention, true);
  assert.equal(state.technicalLineCount(state.technicalSections()[0].events), 3);
  assert.equal(state.technicalDaysAway({ event_proposed_start: sgt(0) }), "Today");
  assert.equal(state.technicalDaysAway({ event_proposed_start: sgt(1) }), "Tomorrow");
  assert.equal(state.technicalDaysAway({ event_proposed_start: sgt(5) }), "In 5 days");
  // Search and sort apply first; a section never moves
  state.technicalView.query = "item 5";
  assert.deepEqual(shape(), [["past", [13]]]);
  state.technicalView.query = "";
  state.technicalView.sort = "recorded";
  assert.deepEqual(shape()[0], ["soon", [14, 10]], "most recently recorded first, within the section");
});

test("TC-9992 each event's lines are an aligned table, with only the arrangement actions and no summary cards", () => {
  // [SCRUM-40, SCRUM-44] The worklist is no longer read-only: each line has a status form and a reserve action.
  const page = html.slice(html.indexOf("currentPath === '/technical' && user.role === 'Technical Support' && technicalWorklist"), html.indexOf("currentPath === '/events' && user.role === 'Organiser'"));
  assert.match(page, /<table class="data-table equipment-table"><thead><tr><th scope="col">Equipment<\/th><th scope="col" class="cell-number">Quantity<\/th><th scope="col">Technical requirements<\/th><th scope="col">Recorded<\/th><th scope="col">Arrangements<\/th><\/tr><\/thead>/);
  for (const label of ["Equipment", "Quantity", "Technical requirements", "Recorded", "Arrangements"]) assert.match(page, new RegExp(`<td data-label="${label}"`));
  assert.match(page, /class="equipment-event table-container"/);
  assert.doesNotMatch(page, /summary-strip|operations-summary/, "no card repeats the section counts");
  assert.doesNotMatch(page, /row-link|<button[^>]*@click="(?!fetchTechnicalWorklist|clearListSearch|listControls|toggleReserve|releaseReservation)/, "the only row actions are status, reserve and release");
  assert.match(page, /item\.technical_requirements \|\| 'None recorded'/);
});

test("TC-9993 loading, error, refresh failure and permission-denied states", async () => {
  const state = app();
  state.apiRequest = async () => ({ ok: false, status: 500, data: { detail: "Database unavailable" } });
  await state.fetchTechnicalWorklist();
  assert.deepEqual([state.technicalWorklist.loaded, state.technicalWorklist.error, state.technicalWorklist.denied], [false, "Database unavailable", false]);
  state.apiRequest = async () => ({ ok: true, status: 200, data: { equipment_requests: lines } });
  await state.fetchTechnicalWorklist();
  assert.deepEqual([state.technicalWorklist.loaded, state.technicalWorklist.error], [true, ""]);
  state.apiRequest = async () => ({ ok: false, status: 503, data: { detail: "Try later" } });
  await state.fetchTechnicalWorklist();
  assert.equal(state.technicalWorklist.items.length, 3, "a failed refresh keeps the worklist");
  assert.equal(state.technicalWorklist.error, "Try later");
  state.apiRequest = async () => ({ ok: false, status: 403, data: { detail: "Only Coordinators, Leads and Technical Support Staff can view equipment requirements" } });
  await state.fetchTechnicalWorklist();
  assert.equal(state.technicalWorklist.denied, true);
  assert.match(html, /x-show="technicalWorklist.denied" role="alert"><div class="state-icon danger">!<\/div><h2>You can't view the equipment worklist<\/h2>/);
  assert.match(html, /Couldn't load the worklist: \$\{technicalWorklist.error\}/);
  assert.match(html, /x-show="!technicalWorklist.loaded && !technicalWorklist.error" role="status"><span class="spinner dark" aria-hidden="true"><\/span> Loading the worklist…/);
  assert.match(html, /<h2>No equipment recorded yet<\/h2>/);
});
