import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
// The equipment loads after the details, without holding up the details' own loading state
const settle = () => new Promise(resolve => setTimeout(resolve));

function app(role, userId = 2) {
  let factory;
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  });
  const state = factory();
  state.user = { user_id: userId, role: normalizeRole(role) };
  return state;
}
const event = { id: 7, title: "Gala", status: "Approved", assigned_coordinator_id: 2, organiser_id: 1, equipment_requirements: "Two mics and a projector" };
const line = { id: 1, event_id: 7, event_title: "Gala", event_proposed_start: null, equipment_type: "Projector", quantity: 1, technical_requirements: "HDMI", requested_by: "coord1", created_at: "2026-11-05T03:00:00" };

// GET /events/7, then GET /events/7/equipment-requests; POST adds a line
function backend(state, { items = [line], post } = {}) {
  const sent = [];
  state.apiRequest = async (path, options = {}) => {
    sent.push([options.method || "GET", path, options.body]);
    if (path === "/events/7") return { ok: true, status: 200, data: event };
    if (path === "/events/7/equipment-requests" && options.method === "POST") { const result = post ? post(options.body) : { ok: true, status: 201, data: {} }; if (result.ok) items = [...items, { ...line, id: items.length + 1, ...options.body }]; return result; }
    if (path === "/events/7/equipment-requests") return { ok: true, status: 200, data: { event_id: 7, equipment_requests: items } };
    return { ok: true, status: 200, data: [] };
  };
  return sent;
}

test("TC-10011 Coordinators and Leads see an event's equipment in the request details; Organisers don't", async () => {
  for (const role of ["coordinator", "lead"]) {
    const state = app(role);
    const sent = backend(state);
    await state.openEventDetail(7); await settle();
    assert.deepEqual(sent.map(([method, path]) => `${method} ${path}`), ["GET /events/7", "GET /events/7/equipment-requests"], role);
    assert.equal(state.eventDetail.equipment.loaded, true);
    assert.deepEqual(plain(state.eventDetail.equipment.items.map(item => item.equipment_type)), ["Projector"]);
  }
  const organiser = app("organiser", 1);
  const sent = backend(organiser);
  await organiser.openEventDetail(7); await settle();
  assert.deepEqual(sent.map(([, path]) => path), ["/events/7"], "the Organiser can't read equipment (equipment.require_equipment_viewer)");
  assert.equal(organiser.eventDetail.equipment, null);
});

test("TC-10012 only the assigned Coordinator or a Lead can add equipment", () => {
  assert.equal(app("coordinator", 2).canRecordEquipment(event), true);
  assert.equal(app("coordinator", 3).canRecordEquipment(event), false, "another Coordinator reads only");
  assert.equal(app("coordinator", 2).canRecordEquipment({ ...event, assigned_coordinator_id: null }), false);
  assert.equal(app("lead", 9).canRecordEquipment(event), true);
  assert.equal(app("organiser", 1).canRecordEquipment(event), false);
  assert.match(html, /<template x-if="eventDetail.equipment.loaded && !eventDetail.equipment.denied && canRecordEquipment\(eventDetail.detail\)"><form class="equipment-form"/);
  assert.match(html, /Only the assigned Coordinator or a Lead can add equipment to this event\./);
});

test("TC-10013 the form checks type, quantity and duplicates before calling, then adds the line", async () => {
  const state = app("coordinator");
  const sent = backend(state);
  await state.openEventDetail(7); await settle();
  const form = state.eventDetail.equipment.form;
  sent.length = 0;
  await state.addEquipment();
  assert.deepEqual(plain(state.eventDetail.equipment.errors), { equipment_type: "Enter the equipment type", quantity: "Enter how many are needed" });
  Object.assign(form, { equipment_type: "  projector ", quantity: "1.5" });
  await state.addEquipment();
  assert.equal(state.eventDetail.equipment.errors.equipment_type, "Projector is already recorded (quantity 1). Lines can't be changed yet.");
  assert.equal(state.eventDetail.equipment.errors.quantity, "Enter a whole number, 1 or more");
  form.equipment_type = "x".repeat(101);
  await state.addEquipment();
  assert.match(state.eventDetail.equipment.errors.equipment_type, /100 characters/);
  assert.deepEqual(sent, [], "nothing invalid reached the API");
  Object.assign(form, { equipment_type: " Wireless microphone ", quantity: "4", technical_requirements: "  " });
  await state.addEquipment();
  assert.deepEqual(plain(sent[0]), ["POST", "/events/7/equipment-requests", { equipment_type: "Wireless microphone", quantity: 4 }], "blank notes are left out");
  assert.deepEqual(plain(state.eventDetail.equipment.items.map(item => item.equipment_type)), ["Projector", "Wireless microphone"]);
  assert.deepEqual(plain(state.eventDetail.equipment.form), { equipment_type: "", quantity: "", technical_requirements: "" });
  assert.equal(state.alert.message, "4 × Wireless microphone added. Technical Support can see it now.");
});

test("TC-10014 refusals show beside the field or above the form; load failures and 403 have their states", async () => {
  const state = app("lead", 9);
  backend(state, { post: () => ({ ok: false, status: 409, data: { detail: "'Stage' is already recorded for this event (quantity 2). Amend that entry instead of adding a second one." } }) });
  await state.openEventDetail(7); await settle();
  Object.assign(state.eventDetail.equipment.form, { equipment_type: "stage", quantity: "1" });
  await state.addEquipment();
  assert.match(state.eventDetail.equipment.errors.equipment_type, /already recorded/);
  backend(state, { post: () => ({ ok: false, status: 403, data: { detail: "Coordinators can record equipment only for events assigned to them" } }) });
  Object.assign(state.eventDetail.equipment.form, { equipment_type: "Lectern", quantity: "1" });
  await state.addEquipment();
  assert.equal(state.eventDetail.equipment.errors.form, "Coordinators can record equipment only for events assigned to them");

  const failing = app("coordinator");
  failing.apiRequest = async path => path === "/events/7" ? { ok: true, status: 200, data: event } : { ok: false, status: 500, data: { detail: "Database unavailable" } };
  await failing.openEventDetail(7); await settle();
  assert.deepEqual([failing.eventDetail.equipment.loaded, failing.eventDetail.equipment.error], [false, "Database unavailable"]);
  failing.apiRequest = async path => path === "/events/7" ? { ok: true, status: 200, data: event } : { ok: false, status: 403, data: { detail: "Only Coordinators, Leads and Technical Support Staff can view equipment requirements" } };
  await failing.openEventDetail(7); await settle();
  assert.equal(failing.eventDetail.equipment.denied, true);
  assert.match(html, /<h2>You can't view this event's equipment<\/h2>/);
  assert.match(html, /No equipment added yet, so nothing for this event is on the Technical Support worklist\./);
  assert.match(html, /<strong>Organiser's equipment notes<\/strong>/);
});
