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

test("TC-9601 (F1) the app component starts once: Alpine calls init() itself, so the root has no x-init", () => {
  const root = html.match(/<div x-data="appState\(\)"[^>]*>/)[0];
  assert.doesNotMatch(root, /x-init/);
  assert.match(source, /async init\(\)/);
});

test("TC-9602 (F2) notification times are read as UTC, like booking times", () => {
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

test("TC-9603 (F6) /technical is a real screen for Technical Support and /equipment-requests is proxied", () => {
  for (const role of ["technical support", "technical_support"]) {
    assert.deepEqual(plain(resolveRolePath("/technical", normalizeRole(role))), { accessDenied: false, currentPath: "/technical", unbuiltRole: false });
  }
  assert.equal(resolveRolePath("/attendee", "Attendee").unbuiltRole, true);
  assert.match(readFileSync(new URL("../vite.config.js", import.meta.url), "utf8"), /"\/equipment-requests": backendProxy/);
  assert.match(html, /x-if="currentPath === '\/technical' && user.role === 'Technical Support' && technicalWorklist"/);
});

test("TC-9604 (F6) the worklist loads GET /equipment-requests and groups lines by event in backend order", async () => {
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

test("TC-9605 (F6) a failed load shows the backend's message and keeps no data", async () => {
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

test("TC-9606 (F6) other roles never request the worklist, and signing out clears it", async () => {
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
