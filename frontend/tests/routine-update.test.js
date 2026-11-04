import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// SCRUM-94: the Organiser or assigned Coordinator updates routine event
// information during planning, from SCRUM-88's event detail view, with
// PATCH /events/{id} (SCRUM-28). Significant fields need a change request.

const APP_URL = new URL("../app.js", import.meta.url);
const APP_SOURCE = readFileSync(APP_URL, "utf8");
const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");

// Every name app.js imports, read from its own import lines (aliases
// included), so a new import in app.js never breaks this harness.
async function appImports() {
  const names = {};
  for (const [, list, from] of APP_SOURCE.matchAll(/^import\s*\{([\s\S]*?)\}\s*from\s*"(.+?)";/gm)) {
    const mod = await import(new URL(from, APP_URL));
    for (const part of list.split(",").map(item => item.trim()).filter(Boolean)) {
      const [exported, local = exported] = part.split(/\s+as\s+/).map(item => item.trim());
      names[local] = mod[exported];
    }
  }
  return names;
}
const IMPORTS = await appImports();

function app(user) {
  let factory;
  vm.runInNewContext(APP_SOURCE.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    ...IMPORTS,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
  });
  const state = factory();
  state.user = user;
  state.fetchEvents = async () => { state.refreshed = "events"; };
  state.fetchAssignedEvents = async () => { state.refreshed = "assigned"; };
  return state;
}

const ORGANISER = { user_id: 10, role: "Organiser" };
const COORDINATOR = { user_id: 20, role: "Coordinator" };

function event(overrides = {}) {
  return {
    id: 5, title: "Annual Gala", purpose: "Fundraising", description: "Dinner and auction",
    proposed_start: "2026-12-01T18:00:00", expected_attendance: 200, venue_requirements: "Ballroom",
    accessibility_needs: null, equipment_requirements: "Microphones", registration_needs: "Badges",
    status: "Approved", organiser_id: 10, assigned_coordinator_id: 20,
    organiser: "organiser1", assigned_coordinator: "coord1", clarification_note: null,
    ...overrides,
  };
}

// An open detail view, as openEventDetail leaves it after GET /events/{id}.
function withDetail(user, overrides) {
  const state = app(user);
  state.eventDetail = { id: 5, detail: event(overrides), loading: false, error: "", actionError: "" };
  return state;
}

// Records every request; answers with the given response.
function recorder(state, response) {
  const sent = [];
  state.apiRequest = async (path, options = {}) => { sent.push({ path, options }); return typeof response === "function" ? response(path, options) : response; };
  return sent;
}

test("TC-9401 (AC1) Edit details is offered to the Organiser and the assigned Coordinator during planning", () => {
  for (const status of ["Approved", "Planning", "Confirmed"]) {
    assert.equal(app(ORGANISER).canEditRoutine(event({ status })), true, `organiser, ${status}`);
    assert.equal(app(COORDINATOR).canEditRoutine(event({ status })), true, `coordinator, ${status}`);
  }
  const html = HTML.slice(HTML.indexOf('aria-labelledby="event-detail-title"'));
  assert.match(html, /x-show="canEditRoutine\(eventDetail\.detail\)[^"]*"[^>]*@click="openRoutineEdit\(\)"[^>]*>Edit details/);
});

test("TC-9402 (AC4) Edit details is not offered in other statuses or to other users, and nothing is sent", async () => {
  for (const status of ["Draft", "Submitted", "Under Review", "Returned", "Rejected", "Completed", "Cancelled"]) {
    assert.equal(app(ORGANISER).canEditRoutine(event({ status })), false, status);
  }
  const others = [
    { user_id: 11, role: "Organiser" },               // another organiser
    { user_id: 21, role: "Coordinator" },             // not the assigned coordinator
    { user_id: 30, role: "Event Coordinator Lead" },
    { user_id: 40, role: "Venue Staff" },
    { user_id: 50, role: "Technical Support" },
  ];
  for (const user of others) {
    const state = withDetail(user);
    assert.equal(state.canEditRoutine(state.eventDetail.detail), false, user.role);
    const sent = recorder(state, { ok: true, status: 200, data: {} });
    state.openRoutineEdit();
    assert.equal(state.eventDetail.routine ?? null, null, `${user.role}: no form`);
    await state.saveRoutineEdit();
    assert.equal(sent.length, 0, `${user.role}: nothing sent`);
  }
  assert.equal(app(ORGANISER).canEditRoutine(null), false);
});

test("TC-9403 (AC2) the form is prefilled, only the changed routine field is sent, and the view keeps its other details", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  assert.deepEqual({ ...state.eventDetail.routine.form }, {
    title: "Annual Gala", purpose: "Fundraising", description: "Dinner and auction",
    accessibility_needs: "", registration_needs: "Badges",
  });
  state.eventDetail.routine.form.description = "Dinner, auction and live band";
  // PATCH answers with event_to_dict: no organiser or coordinator names.
  const { organiser, assigned_coordinator, clarification_note, ...short } = event({ description: "Dinner, auction and live band" });
  const sent = recorder(state, { ok: true, status: 200, data: { ...short, created_at: "2026-10-01T00:00:00", message: "Event updated successfully", changed_fields: ["description"] } });
  await state.saveRoutineEdit();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].path, "/events/5");
  assert.equal(sent[0].options.method, "PATCH");
  assert.deepEqual({ ...sent[0].options.body }, { description: "Dinner, auction and live band" });
  assert.equal(sent[0].options.inlineErrors, true);
  const detail = state.eventDetail.detail;
  assert.equal(detail.description, "Dinner, auction and live band");
  assert.equal(detail.organiser, "organiser1", "organiser name kept");
  assert.equal(detail.assigned_coordinator, "coord1", "coordinator name kept");
  assert.equal("created_at" in detail, false, "no extra fields added to the view");
  assert.equal(state.eventDetail.routine, null, "form closes");
  assert.equal(state.refreshed, "events", "the organiser's list refreshes");

  const coordinator = withDetail(COORDINATOR);
  coordinator.openRoutineEdit();
  coordinator.eventDetail.routine.form.accessibility_needs = "Step-free access";
  const coordinatorSent = recorder(coordinator, { ok: true, status: 200, data: event({ accessibility_needs: "Step-free access" }) });
  await coordinator.saveRoutineEdit();
  assert.deepEqual({ ...coordinatorSent[0].options.body }, { accessibility_needs: "Step-free access" });
  assert.equal(coordinator.refreshed, "assigned", "the coordinator's queue refreshes");
});

test("TC-9404 (AC2) saving with no changes sends nothing and says so inside the view", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.title = "  Annual Gala  ";
  state.eventDetail.routine.form.registration_needs = "Badges";
  const sent = recorder(state, { ok: true, status: 200, data: {} });
  await state.saveRoutineEdit();
  assert.equal(sent.length, 0);
  assert.equal(state.eventDetail.routine.message, "No changes to save.");
  assert.equal(state.alert?.message ?? "", "", "not in the page alert behind the view");
  assert.match(HTML, /x-text="eventDetail\.routine\.message"/);
});

test("TC-9405 (AC3) significant fields are read-only with a change-request note, and are never sent", async () => {
  const start = HTML.indexOf('id="routine-edit-form"');
  assert.ok(start > 0, "the routine edit form exists");
  const form = HTML.slice(start, HTML.indexOf("</form>", start));
  for (const field of ["title", "purpose", "description", "accessibility_needs", "registration_needs"]) {
    assert.match(form, new RegExp(`x-model[^=]*="eventDetail\\.routine\\.form\\.${field}"`), `${field} is editable`);
  }
  for (const field of ["proposed_start", "expected_attendance", "venue_requirements", "equipment_requirements"]) {
    assert.doesNotMatch(form, new RegExp(`x-model[^=]*="[^"]*${field}`), `${field} is not editable`);
    assert.match(form, new RegExp(`eventDetail\\.detail\\.${field}`), `${field} is shown`);
  }
  assert.match(form, /change request/i);

  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.title = "Annual Gala 2026";
  Object.assign(state.eventDetail.routine.form, { proposed_start: "2027-01-01T10:00", expected_attendance: 999 });
  const sent = recorder(state, { ok: true, status: 200, data: event({ title: "Annual Gala 2026" }) });
  await state.saveRoutineEdit();
  assert.deepEqual({ ...sent[0].options.body }, { title: "Annual Gala 2026" });
});

test("TC-9406 (AC3) a server 422 or 400 is shown in the view, and the form keeps what was typed", async () => {
  for (const [status, detail] of [
    [422, "expected_attendance cannot be changed here: this is a significant change (SCRUM-29)"],
    [400, "Event information can only be updated during planning (Approved, Planning, Confirmed); this event is Completed."],
    [422, [{ loc: ["body", "title"], msg: "Value error, Title cannot be empty" }]],
  ]) {
    const state = withDetail(ORGANISER);
    state.openRoutineEdit();
    state.eventDetail.routine.form.purpose = "Networking";
    recorder(state, { ok: false, status, data: { detail } });
    await state.saveRoutineEdit();
    const routine = state.eventDetail.routine;
    assert.ok(routine, `${status}: the form stays open`);
    assert.equal(routine.form.purpose, "Networking", `${status}: typed input kept`);
    assert.ok(routine.error.length > 0, `${status}: message shown`);
    if (typeof detail === "string") assert.equal(routine.error, detail);
    else assert.match(routine.error, /Title cannot be empty/);
    assert.equal(routine.busy, false);
    assert.equal(state.eventDetail.detail.purpose, "Fundraising", "the view is unchanged");
  }
  assert.match(HTML, /x-text="eventDetail\.routine\.error"/);
});

test("TC-9407 (AC4) a 403 shows a message in the view, and a 401 still signs the user out", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.purpose = "Networking";
  recorder(state, { ok: false, status: 403, data: { detail: "You are not authorised to view or update this event" } });
  await state.saveRoutineEdit();
  assert.equal(state.eventDetail.routine.error, "You are not authorised to view or update this event");

  // Through the real apiRequest, so its 401 handling runs.
  let factory;
  vm.runInNewContext(APP_SOURCE.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    ...IMPORTS,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: {} },
    fetch: async () => ({ ok: false, status: 401, json: async () => ({ detail: "Not authenticated" }) }),
  });
  const real = factory();
  real.user = ORGANISER;
  real.eventDetail = { id: 5, detail: event(), loading: false, error: "", actionError: "" };
  real.openRoutineEdit();
  real.eventDetail.routine.form.purpose = "Networking";
  await real.saveRoutineEdit();
  assert.equal(real.user, null, "signed out");
  assert.equal(real.eventDetail, null, "the private detail is cleared");
});

test("TC-9408 (AC1) Organisers can open the detail view of their own requests from My event requests", async () => {
  const state = app(ORGANISER);
  const sent = recorder(state, { ok: true, status: 200, data: event() });
  await state.openEventDetail(5);
  assert.deepEqual(sent.map(request => request.path), ["/events/5"]);
  assert.equal(state.eventDetail.detail.title, "Annual Gala");
  // Every row in the grouped list has "View details"
  const start = HTML.indexOf('x-for="event in group.events"');
  const card = HTML.slice(start, HTML.indexOf("</li></template>", start));
  assert.match(card, /@click="openEventDetail\(event\.id\)"/);
});

test("TC-9409 (Boundary) a blank title is caught before sending, and the edit can be cancelled", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.title = "   ";
  const sent = recorder(state, { ok: true, status: 200, data: {} });
  await state.saveRoutineEdit();
  assert.equal(sent.length, 0);
  assert.equal(state.eventDetail.routine.error, "Title cannot be empty");
  state.cancelRoutineEdit();
  assert.equal(state.eventDetail.routine, null);
  assert.equal(state.eventDetail.detail.title, "Annual Gala");
});

test("TC-9410 (Cross-cutting) a save that returns after the view was closed by the app still refreshes the lists; after logout it changes nothing", async () => {
  for (const [label, leave, expected] of [
    ["navigated away", state => state.closeEventDetail(), "events"],
    ["logged out", state => { state.user = null; state.eventDetail = null; }, undefined],
  ]) {
    const state = withDetail(ORGANISER);
    state.openRoutineEdit();
    state.eventDetail.routine.form.purpose = "Networking";
    let finish;
    state.apiRequest = () => new Promise(resolve => { finish = resolve; });
    const pending = state.saveRoutineEdit();
    assert.equal(state.eventDetail.routine.busy, true);
    await state.saveRoutineEdit();             // a second click while busy does nothing
    leave(state);
    finish({ ok: true, status: 200, data: event({ purpose: "Networking" }) });
    await pending;
    assert.equal(state.eventDetail, null, `${label}: the view stays closed`);
    assert.equal(state.refreshed, expected, `${label}: list refresh`);
  }
});

// Darryl's PR #42 review: closing the view while a save is in flight dropped the
// result, and closing with unsaved edits discarded them silently.
test("TC-9412 (Cross-cutting) the user can't close the view while a save is in flight, and the saved result is shown", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.purpose = "Networking";
  let finish;
  state.apiRequest = () => new Promise(resolve => { finish = resolve; });
  const pending = state.saveRoutineEdit();
  const view = state.eventDetail;
  state.requestCloseEventDetail();           // ✕, backdrop or Escape
  assert.equal(state.eventDetail, view, "the view stays open while saving");
  finish({ ok: true, status: 200, data: event({ purpose: "Networking" }) });
  await pending;
  assert.equal(state.eventDetail.detail.purpose, "Networking");
  assert.equal(state.eventDetail.notice, "Event details updated.");
  assert.equal(state.refreshed, "events");
  state.requestCloseEventDetail();
  assert.equal(state.eventDetail, null, "closes once the save is done");

  // ✕, the backdrop and Escape all go through the guarded close.
  const dialog = HTML.slice(HTML.indexOf('<template x-if="eventDetail">'), HTML.indexOf('aria-labelledby="event-detail-title"') + 400);
  assert.equal((dialog.match(/requestCloseEventDetail\(\)/g) || []).length, 3);
  assert.doesNotMatch(dialog, /[^t]closeEventDetail\(\)/);
});

test("TC-9413 (Negative) closing with unsaved edits asks first in the view; Keep editing keeps them, Discard closes", () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  state.eventDetail.routine.form.description = "Dinner, auction and live band";
  assert.equal(state.routineIsDirty(), true);
  state.requestCloseEventDetail();
  assert.ok(state.eventDetail, "not closed yet");
  assert.equal(state.eventDetail.routine.confirmDiscard, true, "the prompt is shown");
  state.keepRoutineEdit();
  assert.equal(state.eventDetail.routine.confirmDiscard, false);
  assert.equal(state.eventDetail.routine.form.description, "Dinner, auction and live band", "typed text kept");
  state.requestCloseEventDetail();
  state.closeEventDetail();                  // Discard changes
  assert.equal(state.eventDetail, null);

  // No prompt when nothing changed (spaces only don't count), or with no form open.
  for (const edit of [() => {}, routine => { routine.form.title = "  Annual Gala "; }, null]) {
    const clean = withDetail(ORGANISER);
    if (edit) { clean.openRoutineEdit(); edit(clean.eventDetail.routine); }
    assert.equal(clean.routineIsDirty(), false);
    clean.requestCloseEventDetail();
    assert.equal(clean.eventDetail, null, "closes straight away");
  }

  // The prompt is in the page (D6), not a browser dialog.
  assert.doesNotMatch(APP_SOURCE, /\bconfirm\(/);
  const start = HTML.indexOf('id="routine-edit-form"');
  const form = HTML.slice(start, HTML.indexOf("</form>", start));
  assert.match(form, /x-show="eventDetail\.routine\.confirmDiscard"/);
  assert.match(form, /@click="keepRoutineEdit\(\)"[^>]*>Keep editing/);
  assert.match(form, /@click="closeEventDetail\(\)"[^>]*>Discard changes/);
});

test("TC-9414 (Cross-cutting) saving and the unsaved-edits check use the same comparison", async () => {
  const state = withDetail(ORGANISER);
  state.openRoutineEdit();
  Object.assign(state.eventDetail.routine.form, { title: " Annual Gala ", purpose: "Networking", accessibility_needs: "  " });
  const { changes } = state.routineChanges();
  assert.deepEqual({ ...changes }, { purpose: "Networking" });
  const sent = recorder(state, { ok: true, status: 200, data: event({ purpose: "Networking" }) });
  await state.saveRoutineEdit();
  assert.deepEqual({ ...sent[0].options.body }, { ...changes });
  state.openRoutineEdit();
  state.eventDetail.routine.form.title = " ";
  assert.equal(state.routineChanges().error, "Title cannot be empty");
});

// PR #42 review (El): a Coordinator's All Events list refreshes too, as decideEvent does.
test("TC-9415 (Cross-cutting) a Coordinator's save also refreshes All Events when it is open or loaded, and the title is capped at 255", async () => {
  for (const [label, setup, expected] of [
    ["on All Events", state => { state.currentPath = "/coordinator/all-events"; }, ["assigned", "all"]],
    ["All Events loaded earlier", state => { state.currentPath = "/coordinator"; state.allCoordinatorEvents = [event()]; }, ["assigned", "all"]],
    ["All Events never opened", state => { state.currentPath = "/coordinator"; state.allCoordinatorEvents = []; }, ["assigned"]],
  ]) {
    const state = withDetail(COORDINATOR);
    setup(state);
    const calls = [];
    state.fetchAssignedEvents = async () => { calls.push("assigned"); };
    state.fetchAllCoordinatorEvents = async () => { calls.push("all"); };
    state.openRoutineEdit();
    state.eventDetail.routine.form.title = "Annual Gala 2026";
    recorder(state, { ok: true, status: 200, data: event({ title: "Annual Gala 2026" }) });
    await state.saveRoutineEdit();
    assert.deepEqual(calls.sort(), expected.sort(), label);
  }
  // The Organiser still refreshes only My event requests.
  const organiser = withDetail(ORGANISER);
  organiser.currentPath = "/coordinator/all-events";
  organiser.fetchAllCoordinatorEvents = async () => { organiser.refreshed = "all"; };
  organiser.openRoutineEdit();
  organiser.eventDetail.routine.form.title = "Annual Gala 2026";
  recorder(organiser, { ok: true, status: 200, data: event({ title: "Annual Gala 2026" }) });
  await organiser.saveRoutineEdit();
  assert.equal(organiser.refreshed, "events");
  // Matches the backend's title Column(String(255)).
  assert.match(HTML, /<input id="routine-title"[^>]*maxlength="255"/);
});
