import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { changeRequests } from "../src/change-requests.js";

// SCRUM-49, SCRUM-50, SCRUM-29: the change requests panel in the event detail dialog.

const HTML = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const MAIN = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const APP_URL = new URL("../app.js", import.meta.url);
const APP_SOURCE = readFileSync(APP_URL, "utf8");

const PANEL = HTML.slice(HTML.indexOf('x-data="changeRequests()"'), HTML.indexOf("</section>", HTML.indexOf('x-data="changeRequests()"')));

const ORGANISER = { user_id: 10, role: "Organiser" };
const COORDINATOR = { user_id: 20, role: "Coordinator" };

function eventDetail(overrides = {}) {
  return {
    id: 5,
    detail: {
      id: 5, title: "Annual Gala", status: "Planning", organiser_id: 10, assigned_coordinator_id: 20,
      proposed_start: "2099-03-10T14:00:00", expected_attendance: 100, venue_requirements: "Main hall",
      equipment_requirements: null, description: "Dinner", ...overrides,
    },
  };
}

function pendingRequest(overrides = {}) {
  return {
    id: 3, event_id: 5, status: "Pending", reason: "The speaker moved her flight", requested_by: "org",
    changes: { expected_attendance: 150, description: "Dinner and a band" },
    previous_values: { expected_attendance: 100, description: "Dinner" },
    decided_by: null, decision_reason: null, created_at: "2099-01-01T00:00:00", decided_at: null, ...overrides,
  };
}

// The component with what the dialog's parent scope gives it, and a recording API stub.
function panel(user, detail = eventDetail(), replies = {}) {
  const state = changeRequests();
  state.user = user;
  state.eventDetail = detail;
  state.sent = [];
  state.alerts = [];
  state.refreshed = 0;
  state.showAlert = (message, kind) => state.alerts.push([message, kind]);
  state.formatDate = value => `date:${value}`;
  state.formatUtc = value => `utc:${value}`;
  state.statusClass = status => `badge-${status}`;
  state.fetchAssignedEvents = async () => { state.refreshed += 1; };
  state.fetchEvents = async () => { state.refreshed += 1; };
  state.dialogErrorMessage = response => response.data.detail;
  state.apiRequest = async (path, options = {}) => {
    state.sent.push({ path, method: options.method || "GET", body: options.body, inlineErrors: options.inlineErrors });
    const key = `${options.method || "GET"} ${path}`;
    return replies[key] || { ok: true, status: 200, data: [] };
  };
  return state;
}

test("TC-4914 the Organiser's form offers only the permitted fields, each labelled routine or important", () => {
  const form = PANEL.slice(PANEL.indexOf("<form"), PANEL.indexOf("</form>"));
  const important = ["change-start", "change-attendance", "change-venue", "change-equipment"];
  for (const id of important) {
    assert.match(form, new RegExp(`<label for="${id}">[^<]*<em>Important</em></label>`), id);
  }
  assert.match(form, /<label for="change-description">[^<]*<em>Routine<\/em><\/label>/);
  assert.match(form, /<label for="change-reason">[^<]*<em>Required<\/em><\/label>/);
  assert.equal([...form.matchAll(/<(?:input|textarea) /g)].length, 6, "five fields and a reason");
  assert.doesNotMatch(form, /title|purpose|accessibility|registration/i);
});

test("TC-4915 only the changed fields are sent, with the reason, and the form then waits for the decision", async () => {
  const replies = { "POST /events/5/change-requests": { ok: true, status: 201, data: pendingRequest() } };
  const state = panel(ORGANISER, eventDetail(), replies);
  await state.init();
  assert.equal(state.changeForm.proposed_start, "2099-03-10T14:00", "the form starts from the event's values");
  assert.equal(state.changeForm.equipment_requirements, "");
  state.changeForm.expected_attendance = "150";
  state.changeForm.description = "Dinner and a band";
  state.changeForm.reason = "  The speaker moved her flight ";
  await state.submitChange();
  const post = state.sent.find(item => item.method === "POST");
  assert.deepEqual(post.body, {
    changes: { expected_attendance: 150, description: "Dinner and a band" },
    reason: "The speaker moved her flight",
  });
  assert.equal(post.inlineErrors, true);
  assert.equal(state.sent.filter(item => item.method === "GET").length, 2, "the list reloads after sending");
  assert.match(state.changeNotice, /sent to the Coordinator/);
});

test("TC-4915 a new date goes out as entered, in Singapore wall time", async () => {
  const state = panel(ORGANISER);
  await state.init();
  state.changeForm.proposed_start = "2099-05-01T18:30";
  state.changeForm.reason = "New date";
  await state.submitChange();
  assert.deepEqual(state.sent.find(item => item.method === "POST").body.changes, { proposed_start: "2099-05-01T18:30" });
});

test("TC-4915 a routine edit saved after the panel opened is not undone by the next change request", async () => {
  const detail = eventDetail();
  const state = panel(ORGANISER, detail);
  await state.init();
  detail.detail.description = "Edited through Edit details";
  state.changeForm.expected_attendance = "150";
  state.changeForm.reason = "More guests";
  await state.submitChange();
  assert.deepEqual(state.sent.find(item => item.method === "POST").body.changes, { expected_attendance: 150 });
});

test("TC-4915 focus moves to the panel's heading when the form goes away after sending", async () => {
  const state = panel(ORGANISER, eventDetail(), { "POST /events/5/change-requests": { ok: true, status: 201, data: pendingRequest() } });
  let focusQueued = 0;
  state.$nextTick = () => { focusQueued += 1; };
  await state.init();
  state.changeForm.expected_attendance = "150";
  state.changeForm.reason = "More guests";
  await state.submitChange();
  assert.equal(focusQueued, 1);
});

test("TC-4916 an error at the top of a long form is scrolled into view on a phone", () => {
  assert.match(PANEL, /x-text="changeError" x-effect="changeError && \$nextTick\(\(\) => \$el\.scrollIntoView/);
  assert.match(PANEL, /x-text="changeDecisionError" x-effect="changeDecisionError && \$nextTick\(\(\) => \$el\.scrollIntoView/);
});

test("TC-4916 mistakes are caught before sending, in sentences the Organiser can act on", async () => {
  const state = panel(ORGANISER);
  await state.init();
  const posts = () => state.sent.filter(item => item.method === "POST").length;

  state.changeForm.reason = "A reason";
  await state.submitChange();
  assert.match(state.changeError, /Change at least one field/);

  state.changeForm.expected_attendance = "0";
  await state.submitChange();
  assert.match(state.changeError, /whole number/);

  state.changeForm.expected_attendance = "150";
  state.changeForm.reason = "   ";
  await state.submitChange();
  assert.match(state.changeError, /reason/);

  state.changeForm.reason = "A reason";
  state.changeForm.expected_attendance = "100";
  state.changeForm.proposed_start = "";
  await state.submitChange();
  assert.match(state.changeError, /date and time/);
  assert.equal(posts(), 0);
});

test("TC-4916 a refusal from the server is shown and what was typed is kept", async () => {
  const replies = { "POST /events/5/change-requests": { ok: false, status: 409, data: { detail: "This event already has a pending change request." } } };
  const state = panel(ORGANISER, eventDetail(), replies);
  await state.init();
  state.changeForm.expected_attendance = "150";
  state.changeForm.reason = "A reason";
  await state.submitChange();
  assert.match(state.changeError, /already has a pending/);
  assert.equal(state.changeForm.expected_attendance, "150");
  assert.equal(state.changeForm.reason, "A reason");
  assert.equal(state.changeBusy, false);
});

test("TC-4917 the Organiser sees their requests with outcomes, in plain labels", async () => {
  const rejected = pendingRequest({ id: 2, status: "Rejected", decided_by: "coord", decision_reason: "No room is free", decided_at: "2099-01-02T00:00:00" });
  const state = panel(ORGANISER, eventDetail(), { "GET /events/5/change-requests": { ok: true, status: 200, data: [rejected] } });
  await state.init();
  assert.equal(state.changeItems.length, 1);
  assert.deepEqual(state.changeLines(rejected), [
    { key: "expected_attendance", label: "Expected attendance", kind: "Important", from: "100", to: "150" },
    { key: "description", label: "Description", kind: "Routine", from: "Dinner", to: "Dinner and a band" },
  ]);
  assert.match(state.outcomeText(rejected), /Rejected by coord: No room is free/);
  assert.equal(state.outcomeText(pendingRequest()), "Waiting for the Coordinator's decision.");
  assert.match(PANEL, /x-text="outcomeText\(item\)"/);
});

test("TC-4917 dates and empty values read as a person would write them", async () => {
  const state = panel(ORGANISER);
  const item = pendingRequest({ changes: { proposed_start: "2099-05-01T18:30:00", venue_requirements: null }, previous_values: { proposed_start: null, venue_requirements: "Main hall" } });
  assert.deepEqual(state.changeLines(item).map(line => [line.from, line.to]), [["Not set", "date:2099-05-01T18:30:00"], ["Main hall", "Not set"]]);
});

test("TC-4918 the form gives way to a sentence when a request can't be raised", async () => {
  const blocked = async (detail, items = []) => {
    const state = panel(ORGANISER, detail, { "GET /events/5/change-requests": { ok: true, status: 200, data: items } });
    await state.init();
    return state.askBlocked();
  };
  assert.equal(await blocked(eventDetail()), "");
  assert.match(await blocked(eventDetail({ assigned_coordinator_id: null })), /Coordinator/);
  assert.match(await blocked(eventDetail({ status: "Cancelled" })), /Cancelled/);
  assert.match(await blocked(eventDetail({ status: "Draft" })), /Draft/);
  assert.match(await blocked(eventDetail(), [pendingRequest()]), /waiting for the Coordinator/);
  for (const status of ["Submitted", "Under Review", "Approved", "Safety Review", "Confirmed"]) {
    assert.equal(await blocked(eventDetail({ status })), "", status);
  }
  assert.match(PANEL, /x-if="canAsk\(\) && !askBlocked\(\)"/);
});

test("TC-4919 the panel only opens and loads for the owning Organiser and the assigned Coordinator", async () => {
  assert.match(PANEL, /x-show="panelShown\(\)"/);
  const load = async user => {
    const state = panel(user);
    await state.init();
    return [state.panelShown(), state.sent.length];
  };
  assert.deepEqual(await load(ORGANISER), [true, 1]);
  assert.deepEqual(await load(COORDINATOR), [true, 1]);
  assert.deepEqual(await load({ user_id: 11, role: "Organiser" }), [false, 0]);
  assert.deepEqual(await load({ user_id: 21, role: "Coordinator" }), [false, 0]);
  assert.deepEqual(await load({ user_id: 30, role: "Event Coordinator Lead" }), [false, 0]);
});

test("TC-4920 the component is registered at SCRUM-49's fixed places, with labelled status and error regions", () => {
  assert.match(MAIN, /F SCRUM-49 component import: insert directly below this line\.\r?\nimport \{ changeRequests \} from "\.\/change-requests\.js";/);
  assert.match(MAIN, /F SCRUM-49 Alpine\.data: insert directly below this line\.\r?\nAlpine\.data\("changeRequests", changeRequests\);/);
  assert.match(HTML, /SCRUM-49 change requests panel: insert directly below this line\. -->\r?\n\s*<section [^>]*x-data="changeRequests\(\)"/);
  assert.match(PANEL, /role="status"[^>]*x-text="changeNotice"|x-text="changeNotice"[^>]*role="status"/);
  assert.match(PANEL, /role="alert"[^>]*x-text="changeError"|x-text="changeError"[^>]*role="alert"/);
});

test("TC-5016 the assigned Coordinator sees pending requests with approve, reject and a reason; the Organiser doesn't", async () => {
  const data = [pendingRequest(), pendingRequest({ id: 2, status: "Approved", decided_by: "coord", decision_reason: "Fine" })];
  const replies = { "GET /events/5/change-requests": { ok: true, status: 200, data } };
  const coordinator = panel(COORDINATOR, eventDetail(), replies);
  await coordinator.init();
  assert.deepEqual(coordinator.pendingItems().map(item => item.id), [3]);
  assert.equal(coordinator.canDecide(), true);
  const organiser = panel(ORGANISER, eventDetail(), replies);
  await organiser.init();
  assert.equal(organiser.canDecide(), false);
  assert.match(PANEL, /x-if="canDecide\(\) && pendingItems\(\)\.length"/);
  assert.match(PANEL, /<label for="change-decision-reason">/);
  assert.match(PANEL, /@click="decideChange\(item, 'approve'\)"/);
  assert.match(PANEL, /@click="decideChange\(item, 'reject'\)"/);
});

test("TC-5017 approving sends the decision, says how many arrangements were flagged, and refreshes the event", async () => {
  const fresh = { ...eventDetail().detail, expected_attendance: 150 };
  const detail = eventDetail();
  const replies = {
    "GET /events/5/change-requests": { ok: true, status: 200, data: [pendingRequest()] },
    "POST /events/5/change-requests/3/decision": { ok: true, status: 200, data: { ...pendingRequest(), status: "Approved", arrangements_flagged: 3 } },
    "GET /events/5": { ok: true, status: 200, data: fresh },
  };
  const state = panel(COORDINATOR, detail, replies);
  await state.init();
  state.changeDecisionReason = " The hall can take it ";
  await state.decideChange(state.pendingItems()[0], "approve");
  const post = state.sent.find(item => item.method === "POST");
  assert.deepEqual(post.body, { decision: "approve", reason: "The hall can take it" });
  assert.match(state.changeNotice, /3 arrangements flagged/);
  assert.equal(detail.detail.expected_attendance, 150, "the dialog shows the new value");
  assert.equal(state.refreshed, 1, "the list behind the dialog refreshes");
  assert.equal(state.changeDecisionReason, "");
});

test("TC-5018 the message fits what happened: one flagged, none flagged, or rejected", async () => {
  const decided = async (decision, flagged) => {
    const replies = {
      "GET /events/5/change-requests": { ok: true, status: 200, data: [pendingRequest()] },
      "POST /events/5/change-requests/3/decision": { ok: true, status: 200, data: { arrangements_flagged: flagged } },
    };
    const state = panel(COORDINATOR, eventDetail(), replies);
    await state.init();
    state.changeDecisionReason = "A reason";
    await state.decideChange(state.pendingItems()[0], decision);
    return state.changeNotice;
  };
  assert.match(await decided("approve", 1), /1 arrangement flagged/);
  assert.match(await decided("approve", 0), /No arrangements were flagged/);
  assert.match(await decided("reject", 0), /Rejected\. The event hasn't changed/);
});

test("TC-5024 a second approval says the arrangements are still flagged, not that none were", async () => {
  const notice = async (flagged, active) => {
    const replies = {
      "GET /events/5/change-requests": { ok: true, status: 200, data: [pendingRequest()] },
      "POST /events/5/change-requests/3/decision": { ok: true, status: 200, data: { arrangements_flagged: flagged, arrangements_active: active } },
    };
    const state = panel(COORDINATOR, eventDetail(), replies);
    await state.init();
    state.changeDecisionReason = "A reason";
    await state.decideChange(state.pendingItems()[0], "approve");
    return state.changeNotice;
  };
  assert.equal(await notice(0, 2), "Approved and applied. No new flags: 2 arrangements are already flagged for another look.");
  assert.equal(await notice(0, 1), "Approved and applied. No new flags: 1 arrangement is already flagged for another look.");
  assert.equal(await notice(2, 2), "Approved and applied. 2 arrangements flagged for another look.");
  assert.equal(await notice(0, 0), "Approved and applied. No arrangements were flagged.");
});

test("TC-5019 a decision needs a reason, and a refusal keeps it on screen", async () => {
  const replies = {
    "GET /events/5/change-requests": { ok: true, status: 200, data: [pendingRequest()] },
    "POST /events/5/change-requests/3/decision": { ok: false, status: 400, data: { detail: "Proposed start time cannot be in the past" } },
  };
  const state = panel(COORDINATOR, eventDetail(), replies);
  await state.init();
  await state.decideChange(state.pendingItems()[0], "reject");
  assert.match(state.changeDecisionError, /reason/);
  assert.equal(state.sent.filter(item => item.method === "POST").length, 0);

  state.changeDecisionReason = "Looks fine";
  await state.decideChange(state.pendingItems()[0], "approve");
  assert.match(state.changeDecisionError, /in the past/);
  assert.equal(state.changeDecisionReason, "Looks fine");
  assert.equal(state.changeBusy, false);
  assert.match(PANEL, /role="alert"[^>]*x-text="changeDecisionError"|x-text="changeDecisionError"[^>]*role="alert"/);
});

test("TC-5020 a failed load says so and offers a retry", async () => {
  const state = panel(ORGANISER, eventDetail(), { "GET /events/5/change-requests": { ok: false, status: 500, data: { detail: "The service is down" } } });
  await state.init();
  assert.match(state.changeLoadError, /The service is down/);
  assert.match(PANEL, /@click="loadChanges\(\)"/);
});

test("TC-2911 the routine update form says date, attendance and requirement changes need a change request", () => {
  const form = HTML.slice(HTML.indexOf('id="routine-edit-form"'), HTML.indexOf("</form>", HTML.indexOf('id="routine-edit-form"')));
  assert.match(form, /Date, attendance, venue and equipment requirements[^<]*change request/);
  for (const id of ["routine-start", "routine-attendance", "routine-venue", "routine-equipment"]) assert.doesNotMatch(form, new RegExp(id));
});

test("TC-2912 the amendment form says what an important change does", () => {
  const dialog = HTML.slice(HTML.indexOf("x-if=\"modal.kind === 'amend'\""));
  assert.match(dialog.slice(0, 2500), /date, attendance or requirements is an important change/i);
});

// The amend save runs in app.js, so this builds it the way routine-update.test.js does.
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

test("TC-2913 saving an amendment says when arrangements were flagged, and stays quiet when none were", async () => {
  const imports = await appImports();
  const save = async flagged => {
    let factory;
    vm.runInNewContext(APP_SOURCE.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
      ...imports,
      document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
      window: { location: {} },
    });
    const state = factory();
    const alerts = [];
    state.user = ORGANISER;
    state.fetchEvents = async () => {};
    state.showAlert = message => alerts.push(message);
    state.amendPayload = () => ({ expected_attendance: 150 });
    state.amendFormFrom = value => value;
    state.modal = { open: true, kind: "amend", event: { id: 5 }, action: "" };
    state.apiRequest = async () => ({ ok: true, status: 200, data: { change_type: flagged ? "important" : "routine", arrangements_flagged: flagged } });
    await state.amendEvent(5, {});
    return alerts.at(-1);
  };
  assert.equal(await save(0), "Changes saved");
  assert.equal(await save(2), "Changes saved. 2 arrangements flagged for another look.");
  assert.equal(await save(1), "Changes saved. 1 arrangement flagged for another look.");
});
