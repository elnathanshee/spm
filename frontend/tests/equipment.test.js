/**
 * SCRUM-93: UI - record equipment needs, and the Technical Support workspace.
 *
 * Acceptance criteria:
 *   AC1  For an assigned event, the Coordinator records equipment type,
 *        quantity (more than 0) and technical requirements
 *        (POST /events/{id}/equipment-requests). Errors are shown on the
 *        fields. A Lead can do this for any event.
 *   AC2  The event's equipment list (GET /events/{id}/equipment-requests)
 *        appears in the event view for the Coordinator and the Lead.
 *   AC3  Technical Support's /technical page replaces the placeholder with
 *        every equipment requirement (GET /equipment-requests): event, type,
 *        quantity, notes and requester, with an empty state.
 *   AC4  Other roles cannot see or reach these.
 *
 * Completes SCRUM-39 AC1 to AC3.
 *
 * Decisions recorded here:
 *   D1  The form checks that a type is given and the quantity is above zero
 *       before sending, so the Coordinator gets the answer immediately. The
 *       server stays the authority: its 422 maps onto the same fields.
 *   D2  A duplicate type (the backend's 409) is a message about the whole
 *       line, not one field, so it is shown above the form.
 *   D3  The /technical page calls GET /equipment-requests and shows what comes
 *       back. The backend already decides what each role may see, so the
 *       browser does no filtering of its own.
 *   D4  Equipment is loaded in the event view only for roles AC2 names. An
 *       Organiser opens the same view (SCRUM-94) and must not trigger a call
 *       they are not entitled to make.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { NAVIGATION, normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");

function app(role = "Coordinator", userId = 2) {
  let factory;
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  vm.runInNewContext(
    source
      .trimStart()
      .replace(/^const API_BASE.*$/m, 'const API_BASE = "";')
      .replace(/^import[\s\S]*?from ".*?";/gm, ""),
    {
      NAVIGATION,
      normalizeRoleName: normalizeRole,
      resolveRolePath,
      resetSignedOutState,
      requestedFrontendPath: () => null,
      document: { addEventListener: (_, fn) => fn() },
      Alpine: { data: (_, fn) => (factory = fn) },
      window: { location: {}, setInterval: () => 0 },
    },
  );
  const state = factory();
  state.user = { user_id: userId, username: "coord1", role };
  return state;
}

const EVENT = {
  id: 7,
  title: "Annual Leadership Forum",
  status: "Approved",
  organiser_id: 1,
  assigned_coordinator_id: 2,
  proposed_start: "2026-11-18T14:30:00",
};

const ITEM = {
  id: 31,
  event_id: 7,
  event_title: "Annual Leadership Forum",
  event_proposed_start: "2026-11-18T14:30:00",
  equipment_type: "Projector",
  quantity: 2,
  technical_requirements: "HDMI and USB-C, 4K capable",
  requested_by: "coord1",
  created_at: "2026-11-04T09:00:00",
};

/** Open the event view with equipment loading stubbed out. */
async function openDetail(state, { detail = EVENT, equipment = [ITEM], equipmentOk = true } = {}) {
  const calls = [];
  state.apiRequest = async (path, options) => {
    calls.push([path, options]);
    if (path === `/events/${detail.id}`) return { ok: true, status: 200, data: detail };
    if (path === `/events/${detail.id}/equipment-requests`) {
      return equipmentOk
        ? { ok: true, status: 200, data: { equipment_requests: equipment } }
        : { ok: false, status: 500, data: {} };
    }
    return { ok: true, status: 200, data: {} };
  };
  await state.openEventDetail(detail.id);
  return calls;
}

// ---------------------------------------------------------------------------
// AC1 - recording equipment
// ---------------------------------------------------------------------------

test("TC-9301 | Happy Path | AC1: the assigned Coordinator records type, quantity and notes", async () => {
  const state = app();
  await openDetail(state);

  const sent = [];
  state.apiRequest = async (path, options) => {
    sent.push([path, options]);
    if (options?.method === "POST") return { ok: true, status: 201, data: { equipment_request: ITEM } };
    return { ok: true, status: 200, data: { equipment_requests: [ITEM] } };
  };

  state.eventDetail.equipment.form = {
    equipment_type: "  Projector  ",
    quantity: 2,
    technical_requirements: "  HDMI and USB-C, 4K capable  ",
  };
  await state.recordEquipment();

  const post = sent.find(([, options]) => options?.method === "POST");
  assert.ok(post, "a POST was sent");
  assert.equal(post[0], "/events/7/equipment-requests");
  assert.deepEqual({ ...post[1].body }, {
    equipment_type: "Projector",
    quantity: 2,
    technical_requirements: "HDMI and USB-C, 4K capable",
  });
  assert.equal(state.eventDetail.equipment.items.length, 1, "the list is refreshed from the server");
  assert.equal(state.eventDetail.equipment.form.equipment_type, "", "the form is cleared for the next item");
  assert.equal(state.eventDetail.equipment.saving, false);
});

test("TC-9302 | Boundary Path | AC1 (D1): a missing type or a quantity of zero or less is caught on the field", async () => {
  for (const [form, field] of [
    [{ equipment_type: "", quantity: 1 }, "equipment_type"],
    [{ equipment_type: "   ", quantity: 1 }, "equipment_type"],
    [{ equipment_type: "Projector", quantity: 0 }, "quantity"],
    [{ equipment_type: "Projector", quantity: -3 }, "quantity"],
    [{ equipment_type: "Projector", quantity: "" }, "quantity"],
  ]) {
    const state = app();
    await openDetail(state);
    state.apiRequest = () => assert.fail("an invalid line must not be sent");
    state.eventDetail.equipment.form = { technical_requirements: "", ...form };
    await state.recordEquipment();
    assert.ok(state.fieldErrors[field], `${field} should carry the error for ${JSON.stringify(form)}`);
  }
});

test("TC-9303 | Boundary Path | AC1: a quantity of one is accepted, and notes are optional", async () => {
  const state = app();
  await openDetail(state);
  const sent = [];
  state.apiRequest = async (path, options) => {
    sent.push(options);
    return options?.method === "POST"
      ? { ok: true, status: 201, data: { equipment_request: ITEM } }
      : { ok: true, status: 200, data: { equipment_requests: [] } };
  };
  state.eventDetail.equipment.form = { equipment_type: "Lectern", quantity: 1, technical_requirements: "" };
  await state.recordEquipment();

  const post = sent.find((options) => options?.method === "POST");
  assert.ok(post, "a quantity of one is sent");
  assert.equal(post.body.quantity, 1);
  assert.equal(post.body.technical_requirements, undefined, "blank notes are left out rather than sent as empty");
});

test("TC-9304 | Negative Path | AC1 (D1): the server's field errors are shown against the fields", async () => {
  const state = app();
  await openDetail(state);
  state.apiRequest = async (path, options) =>
    options?.method === "POST"
      ? {
          ok: false,
          status: 422,
          data: {
            detail: [
              { loc: ["body", "quantity"], msg: "Input should be greater than 0" },
              { loc: ["body", "equipment_type"], msg: "Equipment type cannot be empty" },
            ],
          },
        }
      : { ok: true, status: 200, data: { equipment_requests: [] } };

  state.eventDetail.equipment.form = { equipment_type: "Projector", quantity: 4, technical_requirements: "" };
  await state.recordEquipment();

  assert.equal(state.fieldErrors.quantity, "Input should be greater than 0");
  assert.equal(state.fieldErrors.equipment_type, "Equipment type cannot be empty");
  assert.equal(state.eventDetail.equipment.form.equipment_type, "Projector", "the typed values are kept");
  assert.equal(state.eventDetail.equipment.saving, false);
});

test("TC-9305 | Negative Path | AC1 (D2): a duplicate type shows the server's message above the form", async () => {
  const detail = "'Projector' is already recorded for this event (quantity 2). Amend that entry instead of adding a second one.";
  const state = app();
  await openDetail(state);
  state.apiRequest = async (path, options) =>
    options?.method === "POST"
      ? { ok: false, status: 409, data: { detail } }
      : { ok: true, status: 200, data: { equipment_requests: [ITEM] } };

  state.eventDetail.equipment.form = { equipment_type: "projector", quantity: 5, technical_requirements: "" };
  await state.recordEquipment();

  assert.equal(state.eventDetail.equipment.formError, detail);
  assert.equal(state.eventDetail.equipment.form.equipment_type, "projector", "the typed values are kept");
});

test("TC-9306 | Cross-Cutting | AC1, AC4: only the assigned Coordinator or a Lead can record", async () => {
  // The assigned Coordinator and the Lead may.
  for (const [role, userId] of [["Coordinator", 2], ["Event Coordinator Lead", 9]]) {
    const state = app(role, userId);
    assert.equal(state.canRecordEquipment(EVENT), true, role);
  }
  // A Coordinator who is not the assigned one may not.
  assert.equal(app("Coordinator", 5).canRecordEquipment(EVENT), false);
  // Nor may anyone outside planning.
  for (const role of ["Organiser", "Attendee", "Technical Support", "Venue Staff"]) {
    assert.equal(app(role).canRecordEquipment(EVENT), false, role);
  }
});

test("TC-9307 | Negative Path | AC4: a role that cannot record never reaches the endpoint", async () => {
  const state = app("Technical Support");
  state.eventDetail = {
    id: 7,
    detail: EVENT,
    equipment: { items: [], loading: false, error: "", formError: "", saving: false, form: { equipment_type: "Projector", quantity: 1, technical_requirements: "" } },
  };
  state.apiRequest = () => assert.fail("Technical Support must not record equipment");
  await state.recordEquipment();
});

// ---------------------------------------------------------------------------
// AC2 - the event's equipment list
// ---------------------------------------------------------------------------

test("TC-9308 | Happy Path | AC2: opening an event loads its equipment list", async () => {
  const state = app();
  const calls = await openDetail(state);

  assert.ok(calls.some(([path]) => path === "/events/7/equipment-requests"), "the list was fetched");
  assert.equal(state.eventDetail.equipment.items.length, 1);
  assert.equal(state.eventDetail.equipment.items[0].equipment_type, "Projector");
  assert.equal(state.eventDetail.equipment.loading, false);
  assert.equal(state.eventDetail.equipment.error, "");
});

test("TC-9309 | Boundary Path | AC2: an event with no equipment says so", async () => {
  const state = app();
  await openDetail(state, { equipment: [] });
  assert.deepEqual(state.eventDetail.equipment.items, []);
  assert.equal(state.eventDetail.equipment.loaded, true, "an empty list is a loaded list, not a failure");
});

test("TC-9310 | Negative Path | AC2: a failed list does not break the rest of the event view", async () => {
  const state = app();
  await openDetail(state, { equipmentOk: false });
  assert.equal(state.eventDetail.detail.id, 7, "the request details still show");
  assert.ok(state.eventDetail.equipment.error.length > 0);
  assert.equal(state.eventDetail.equipment.loaded, false);
});

test("TC-9311 | Cross-Cutting | AC2, AC4 (D4): an Organiser's event view makes no equipment call", async () => {
  const state = app("Organiser", 1);
  const calls = [];
  state.apiRequest = async (path) => {
    calls.push(path);
    return { ok: true, status: 200, data: EVENT };
  };
  await state.openEventDetail(7);
  assert.equal(calls.filter((path) => path.includes("equipment-requests")).length, 0);
  assert.equal(state.eventDetail.equipment, null, "no equipment panel for an Organiser");
});

// ---------------------------------------------------------------------------
// AC3 - the Technical Support workspace
// ---------------------------------------------------------------------------

test("TC-9312 | Happy Path | AC3 (D3): /technical lists every requirement with its event and requester", async () => {
  const state = app("Technical Support", 8);
  const second = { ...ITEM, id: 32, event_id: 9, event_title: "Partner Briefing", equipment_type: "Microphone", quantity: 6, requested_by: "coord2" };
  const calls = [];
  state.apiRequest = async (path) => {
    calls.push(path);
    return { ok: true, status: 200, data: { equipment_requests: [ITEM, second] } };
  };

  state.navigate("/technical");
  await state.fetchEquipmentWorkspace();

  assert.ok(calls.includes("/equipment-requests"));
  const workspace = state.equipmentWorkspace;
  assert.equal(workspace.items.length, 2);
  assert.equal(workspace.loaded, true);
  for (const field of ["event_title", "equipment_type", "quantity", "technical_requirements", "requested_by"]) {
    assert.ok(field in workspace.items[0], `${field} is available to show`);
  }
});

test("TC-9313 | Boundary Path | AC3: an empty worklist is shown as empty, not as an error", async () => {
  const state = app("Technical Support", 8);
  state.apiRequest = async () => ({ ok: true, status: 200, data: { equipment_requests: [] } });
  await state.fetchEquipmentWorkspace();
  assert.deepEqual(state.equipmentWorkspace.items, []);
  assert.equal(state.equipmentWorkspace.loaded, true);
  assert.equal(state.equipmentWorkspace.error, "");
});

test("TC-9314 | Negative Path | AC3: a failed worklist reports it rather than showing an empty page", async () => {
  const state = app("Technical Support", 8);
  state.apiRequest = async () => ({ ok: false, status: 500, data: {} });
  await state.fetchEquipmentWorkspace();
  assert.equal(state.equipmentWorkspace.loaded, false);
  assert.ok(state.equipmentWorkspace.error.length > 0);
});

test("TC-9315 | Workflow Variation | AC3: navigating to /technical loads the worklist", async () => {
  const state = app("Technical Support", 8);
  const calls = [];
  state.apiRequest = async (path) => {
    calls.push(path);
    return { ok: true, status: 200, data: { equipment_requests: [ITEM] } };
  };
  state.navigate("/technical");
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(calls.includes("/equipment-requests"), "the page loads its own data");
  assert.equal(state.accessDenied, false);
  assert.equal(state.unbuiltRole, false, "the placeholder is gone");
});

test("TC-9316 | Cross-Cutting | AC3: /technical is a built screen, and still only for Technical Support", () => {
  assert.equal(resolveRolePath("/technical", "Technical Support").unbuiltRole, false);
  assert.equal(resolveRolePath("/technical", "Technical Support").accessDenied, false);
  for (const role of ["Coordinator", "Event Coordinator Lead", "Organiser", "Venue Staff", "Attendee"]) {
    assert.equal(resolveRolePath("/technical", role).accessDenied, true, role);
  }
  // The Attendee screen is untouched and stays a placeholder.
  assert.equal(resolveRolePath("/attendee", "Attendee").unbuiltRole, true);
});

test("TC-9317 | Cross-Cutting | AC4: a role other than Technical Support never loads the worklist", async () => {
  for (const role of ["Coordinator", "Organiser", "Venue Staff", "Attendee", "Event Coordinator Lead"]) {
    const state = app(role);
    state.apiRequest = () => assert.fail(`${role} must not load the Technical Support worklist`);
    await state.fetchEquipmentWorkspace();
    assert.equal(state.equipmentWorkspace, null, role);
  }
});

// ---------------------------------------------------------------------------
// The screens themselves
// ---------------------------------------------------------------------------

test("TC-9318 | Cross-Cutting | AC2, AC3: both screens are rendered, with their empty states", () => {
  // The event view's equipment panel and its form.
  assert.match(HTML, /x-if="eventDetail\.equipment"/, "the event view has an equipment panel");
  assert.match(HTML, /x-for="item in eventDetail\.equipment\.items"/, "the event's items are listed");
  assert.match(HTML, /recordEquipment\(\)/, "the panel can record a new item");
  assert.match(HTML, /fieldErrors\.equipment_type/, "the type field shows its error");
  assert.match(HTML, /fieldErrors\.quantity/, "the quantity field shows its error");
  assert.match(HTML, /eventDetail\.equipment\.formError/, "a duplicate message has somewhere to appear");

  // The Technical Support workspace.
  assert.match(HTML, /currentPath === '\/technical'/, "the /technical screen exists");
  assert.match(HTML, /x-for="item in equipmentWorkspace\.items"/, "the worklist is listed");
  assert.ok(
    /equipmentWorkspace\.loaded && !equipmentWorkspace\.items\.length/.test(HTML),
    "the worklist has an empty state",
  );
});
