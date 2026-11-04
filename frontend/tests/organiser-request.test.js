import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

// Objects made inside the vm come from another realm; compare plain copies.
const plain = value => JSON.parse(JSON.stringify(value));

function app(role = "organiser", fetchStub) {
  let factory;
  const context = {
    NAVIGATION: [],
    normalizeRoleName: normalizeRole,
    resolveRolePath,
    requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() },
    Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
    ...(fetchStub ? { fetch: fetchStub } : {}),
  };
  vm.runInNewContext(
    source
      .trimStart()
      .replace(/^const API_BASE.*$/m, 'const API_BASE = "";')
      .replace(/^import[\s\S]*?from ".*?";/gm, ""),
    context
  );
  const state = factory();
  state.user = { user_id: 1, role };
  return state;
}

const sampleEvent = {
  id: 42,
  title: "Tech Innovation Conference",
  purpose: "Showcase annual research breakthroughs",
  description: "Full day symposium with keynotes and student poster sessions",
  proposed_start: "2026-11-15T09:00:00",
  expected_attendance: 250,
  venue_requirements: "Auditorium with stage and breakout spaces",
  accessibility_needs: "Wheelchair ramp, hearing loop, live captions",
  equipment_requirements: "4 wireless mics, 2 projectors, HDMI switch",
  registration_needs: "Pre-printed badges, dietary requirements form",
  status: "Submitted",
};

test("TC-9001 (AC1): new request form captures and sends all nine fields with POST /events", async () => {
  const state = app("organiser");
  
  // Verify draft default state contains all 9 fields
  assert.equal(state.draft.title, "");
  assert.equal(state.draft.purpose, "");
  assert.equal(state.draft.description, "");
  assert.equal(state.draft.proposed_start, "");
  assert.equal(state.draft.expected_attendance, null);
  assert.equal(state.draft.venue_requirements, "");
  assert.equal(state.draft.accessibility_needs, "");
  assert.equal(state.draft.equipment_requirements, "");
  assert.equal(state.draft.registration_needs, "");

  const fullData = {
    title: "Regional Summit",
    purpose: "Strategic alignment",
    description: "Multi-track gathering",
    proposed_start: "2026-12-01T10:00",
    expected_attendance: 120,
    venue_requirements: "Seminar room with round tables",
    accessibility_needs: "Step-free access",
    equipment_requirements: "PA system and 2 lapel mics",
    registration_needs: "Digital check-in desk",
  };

  let submittedPath = null;
  let submittedBody = null;
  state.apiRequest = async (path, options = {}) => {
    if (options.body) {
      submittedPath = path;
      submittedBody = options.body;
      return { ok: true, data: { id: 101, ...submittedBody } };
    }
    return { ok: true, data: [] };
  };

  await state.createEventDraft(fullData);

  assert.equal(submittedPath, "/events");
  assert.equal(submittedBody.title, "Regional Summit");
  assert.equal(submittedBody.purpose, "Strategic alignment");
  assert.equal(submittedBody.description, "Multi-track gathering");
  assert.equal(submittedBody.proposed_start, "2026-12-01T10:00");
  assert.equal(submittedBody.expected_attendance, 120);
  assert.equal(submittedBody.venue_requirements, "Seminar room with round tables");
  assert.equal(submittedBody.accessibility_needs, "Step-free access");
  assert.equal(submittedBody.equipment_requirements, "PA system and 2 lapel mics");
  assert.equal(submittedBody.registration_needs, "Digital check-in desk");
  assert.equal(state.currentPath, "/events");
  assert.equal(state.alert.kind, "success");
});

test("TC-9002 (AC2): amend request opens with all nine content fields prefilled", () => {
  const state = app("organiser");
  state.openAmend(sampleEvent);

  assert.equal(state.modal.open, true);
  assert.equal(state.modal.kind, "amend");
  assert.equal(state.modal.event.id, 42);

  // All 9 fields must be prefilled from sampleEvent
  assert.equal(state.amendForm.title, sampleEvent.title);
  assert.equal(state.amendForm.purpose, sampleEvent.purpose);
  assert.equal(state.amendForm.description, sampleEvent.description);
  assert.equal(state.amendForm.proposed_start, "2026-11-15T09:00");
  assert.equal(state.amendForm.expected_attendance, 250);
  assert.equal(state.amendForm.venue_requirements, sampleEvent.venue_requirements);
  assert.equal(state.amendForm.accessibility_needs, sampleEvent.accessibility_needs);
  assert.equal(state.amendForm.equipment_requirements, sampleEvent.equipment_requirements);
  assert.equal(state.amendForm.registration_needs, sampleEvent.registration_needs);
});

test("TC-9003 (AC2): amend saves changes with POST /events/{id}/amend and sends only changed fields", async () => {
  const state = app("organiser");
  state.openAmend(sampleEvent);

  let amendPath = null;
  let amendBody = null;
  state.apiRequest = async (path, options) => {
    if (path.includes("/amend")) {
      amendPath = path;
      amendBody = options.body;
      return { ok: true, data: { id: sampleEvent.id, ...amendBody } };
    }
    return { ok: true, data: [] };
  };

  // 1. Submitting without modifications sends no request and warns "No changes to save."
  await state.amendEvent(sampleEvent.id, state.amendForm);
  assert.equal(amendPath, null);
  assert.equal(state.alert.message, "No changes to save.");
  assert.equal(state.modal.open, true);

  // 2. Modifying only accessibility_needs sends ONLY the changed field
  state.amendForm.accessibility_needs = "Updated: Wheelchair ramp and dedicated quiet room";
  await state.amendEvent(sampleEvent.id, state.amendForm);

  assert.equal(amendPath, `/events/${sampleEvent.id}/amend`);
  assert.deepEqual(plain(amendBody), {
    accessibility_needs: "Updated: Wheelchair ramp and dedicated quiet room",
  });
  assert.equal("title" in amendBody, false);
  assert.equal("proposed_start" in amendBody, false);
  assert.equal(state.modal.open, false);

  // 3. Past start date event: amending another field does NOT send proposed_start (avoids 400 past date validation)
  const pastEvent = {
    ...sampleEvent,
    id: 99,
    proposed_start: "2020-01-01T09:00:00",
  };
  state.openAmend(pastEvent);
  state.amendForm.description = "Updated description for past event";
  amendPath = null;
  amendBody = null;
  await state.amendEvent(pastEvent.id, state.amendForm);

  assert.equal(amendPath, `/events/99/amend`);
  assert.deepEqual(plain(amendBody), {
    description: "Updated description for past event",
  });
  assert.equal("proposed_start" in amendBody, false);
  assert.equal(state.modal.open, false);
});

test("TC-9004 (AC3): server validation errors map to the right fields and keep typed input", async () => {
  const state = app("organiser");
  
  // 1. Test 422 Pydantic validation error mapping
  state.fieldErrors = {};
  state.handleHttpError(422, {
    detail: [
      { loc: ["body", "title"], msg: "Title cannot be empty" },
    ],
  }, "/events");
  assert.equal(state.fieldErrors.title, "Title cannot be empty");

  // 2. Test 400 Past date error mapping on draft creation
  state.fieldErrors = {};
  state.handleHttpError(400, {
    detail: "Proposed start time cannot be in the past",
  }, "/events");
  assert.equal(state.fieldErrors.proposed_start, "Proposed start time cannot be in the past");

  // 3. Test 400 Invalid attendance error mapping on draft creation
  state.fieldErrors = {};
  state.handleHttpError(400, {
    detail: "Expected attendance must be greater than 0",
  }, "/events");
  assert.equal(state.fieldErrors.expected_attendance, "Expected attendance must be greater than 0");

  // 4. Calling createEventDraft with a failing 400 preserves typed input and maps the field error
  const fetchStub400 = async () => ({
    ok: false,
    status: 400,
    json: async () => ({ detail: "Proposed start time cannot be in the past" }),
  });
  const draftState = app("organiser", fetchStub400);
  draftState.draft = {
    title: "My Conference",
    purpose: "Academic sharing",
    description: "Annual keynote",
    proposed_start: "2020-01-01T09:00",
    expected_attendance: 150,
    venue_requirements: "Hall A",
    accessibility_needs: "Braille materials",
    equipment_requirements: "Microphones",
    registration_needs: "VIP roster",
  };
  await draftState.createEventDraft(draftState.draft);

  assert.equal(draftState.fieldErrors.proposed_start, "Proposed start time cannot be in the past");
  assert.equal(draftState.draft.title, "My Conference");
  assert.equal(draftState.draft.accessibility_needs, "Braille materials");
  assert.equal(draftState.draft.proposed_start, "2020-01-01T09:00");

  // 5. POST /events/{id}/submit 400 error does NOT map to fieldErrors; it reaches the page alert
  const submitState = app("organiser", fetchStub400);
  submitState.fetchEvents = async () => {};
  await submitState.submitEvent(42);

  assert.equal(submitState.fieldErrors.proposed_start, undefined);
  assert.equal(submitState.alert?.message, "Proposed start time cannot be in the past");
});

test("TC-9005 (Reset): resetSignedOutState clears all 9 fields on draft and amendForm", () => {
  const state = {
    draft: {
      title: "Saved draft",
      accessibility_needs: "Special access",
      equipment_requirements: "Microphones",
      registration_needs: "VIP roster",
    },
    amendForm: {
      title: "Saved amend",
      accessibility_needs: "Special access",
    },
  };

  resetSignedOutState(state);

  assert.equal(state.draft.title, "");
  assert.equal(state.draft.accessibility_needs, "");
  assert.equal(state.draft.equipment_requirements, "");
  assert.equal(state.draft.registration_needs, "");
  assert.equal(state.amendForm.title, "");
  assert.equal(state.amendForm.accessibility_needs, "");
  assert.equal(state.amendForm.equipment_requirements, "");
  assert.equal(state.amendForm.registration_needs, "");
});

test("TC-9005 amend from a list row loads the full request so no field opens blank", async () => {
  const state = app("organiser");
  // GET /events rows have no venue, accessibility, equipment or registration fields.
  const { venue_requirements, accessibility_needs, equipment_requirements, registration_needs, ...listRow } = sampleEvent;
  let release;
  const paths = [];
  state.apiRequest = (path) => { paths.push(path); return new Promise(resolve => { release = () => resolve({ ok: true, status: 200, data: sampleEvent }); }); };

  state.openAmend(listRow);
  assert.deepEqual(paths, ["/events/42"]);
  assert.equal(state.modal.loading, true);
  await state.submitModal();
  assert.deepEqual(paths, ["/events/42"], "no save while the request is loading");

  release();
  await new Promise(resolve => setTimeout(resolve));
  assert.equal(state.modal.loading, false);
  assert.equal(state.amendForm.venue_requirements, sampleEvent.venue_requirements);
  assert.equal(state.amendForm.equipment_requirements, sampleEvent.equipment_requirements);
  assert.equal(state.amendOriginal.registration_needs, sampleEvent.registration_needs);
});

test("TC-9006 amend shows the load failure and cannot be saved", async () => {
  const state = app("organiser");
  const { venue_requirements, ...listRow } = sampleEvent;
  const paths = [];
  state.apiRequest = async (path) => { paths.push(path); return { ok: false, status: 404, data: { detail: "Event not found" } }; };

  state.openAmend(listRow);
  await new Promise(resolve => setTimeout(resolve));
  assert.equal(state.modal.loadError, "Event not found");
  await state.submitModal();
  assert.deepEqual(paths, ["/events/42"]);
});

test("TC-9007 a full event opens Amend without another request", () => {
  const state = app("organiser");
  let called = false;
  state.apiRequest = async () => { called = true; return { ok: true, data: {} }; };
  state.openAmend(sampleEvent);
  assert.equal(called, false);
  assert.equal(state.modal.loading, false);
});
