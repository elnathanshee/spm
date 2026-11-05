import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app() {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 1, username: "organiser1", role: "Organiser" };
  return state;
}

// Records each request and answers from a list of responses, in order.
function recorder(state, responses) {
  const sent = [];
  state.apiRequest = async (path, options = {}) => {
    sent.push({ path, method: options.method || "GET", body: options.body });
    const next = typeof responses === "function" ? responses(path, options) : responses.shift();
    return next ?? { ok: true, status: 200, data: [] };
  };
  return sent;
}

const row = (id, status, extra = {}) => ({ id, title: `Event ${id}`, purpose: "", description: "", status, proposed_start: null, expected_attendance: 10, created_at: `2026-10-0${id}T00:00:00`, submitted_at: null, clarification_note: null, ...extra });

test("TC-9901 each status has its own section, the ones needing the Organiser first; empty sections are hidden", () => {
  const state = app();
  const statuses = [...readFileSync(new URL("../../backend/status.py", import.meta.url), "utf8").matchAll(/=\s*"([^"]+)"/g)].map(match => match[1]);
  assert.equal(statuses.length, 10);
  state.events = [...statuses.map((status, index) => row(index + 1, status)), row(11, "Mystery")];
  const groups = plain(state.organiserGroups());
  assert.deepEqual(groups.map(group => group.label), ["Returned for clarification", "Drafts", "Submitted", "Under review", "Approved", "Planning", "Confirmed", "Rejected", "Completed", "Cancelled", "Other statuses"]);
  assert.ok(groups.every(group => group.events.length === 1 && group.statuses.length <= 1), "one status per section");
  assert.deepEqual(groups.map(group => group.events[0].status), ["Returned", "Draft", "Submitted", "Under Review", "Approved", "Planning", "Confirmed", "Rejected", "Completed", "Cancelled", "Mystery"]);
  assert.equal(groups.flatMap(group => group.events).length, state.events.length, "no request disappears");
  state.events = [row(1, "Submitted")];
  assert.deepEqual(plain(state.organiserGroups()).map(group => group.key), ["submitted"]);
  // Submitted may have no Coordinator yet, so its description doesn't claim one
  assert.doesNotMatch(plain(state.organiserGroups())[0].description, /Coordinator/);
});

test("TC-9902 search, status group filter and sort work on the loaded list", () => {
  const state = app();
  state.events = [
    row(1, "Submitted", { title: "Harbour Gala", proposed_start: "2027-03-01T18:00:00", submitted_at: "2026-10-02T00:00:00" }),
    row(2, "Submitted", { title: "Spring Fair", description: "harbour walk", proposed_start: "2027-01-01T09:00:00", submitted_at: "2026-10-05T00:00:00" }),
    row(3, "Submitted", { title: "No date yet" }),
    row(14, "Draft", { title: "Workshop" }),
  ];
  const ids = () => plain(state.organiserGroups()).flatMap(group => group.events.map(event => event.id));
  assert.deepEqual(ids(), [14, 2, 1, 3], "Drafts first; within a group the soonest date first, undated last");
  state.eventsView.sort = "submitted";
  assert.deepEqual(ids(), [14, 2, 1, 3]);
  state.eventsView.query = "HARBOUR";
  assert.deepEqual(ids(), [2, 1], "title and description are searched, ignoring case");
  state.eventsView.query = "#14";
  assert.deepEqual(ids(), [14]);
  state.eventsView.query = "";
  state.eventsView.group = "submitted";
  assert.deepEqual(ids(), [2, 1, 3]);
  assert.deepEqual(plain(state.organiserFilterChips()), [
    { key: "all", label: "All", count: 4 }, { key: "draft", label: "Drafts", count: 1 }, { key: "submitted", label: "Submitted", count: 3 },
  ], "only sections with requests get a chip");
  state.clearOrganiserFilters();
  assert.equal(state.eventsView.group, "all");
});

test("TC-9903 a rejection reason comes from the Organiser's rejection notification", () => {
  const state = app();
  const event = row(9, "Rejected", { title: "Gala" });
  assert.equal(state.rejectionReason(event), "", "no notification, no reason");
  state.notifications = [
    { id: 1, event_id: 9, kind: "submitted", message: 'Your event request "Gala" has been submitted.' },
    { id: 2, event_id: 9, kind: "rejected", message: 'Your event request "Gala" has been rejected. Reason: Venue too small. Reason: see notes' },
    { id: 3, event_id: 8, kind: "rejected", message: 'Your event request "Other" has been rejected. Reason: Not this one' },
  ];
  assert.equal(state.rejectionReason(event), "Venue too small. Reason: see notes");
  assert.match(html, /rejectionReason\(event\) \|\| 'No reason is shown here/);
});

test("TC-9904 the list shows loading, permission-denied and load-error states without hiding loaded requests", async () => {
  const state = app();
  assert.equal(state.eventsView.loaded, false);
  recorder(state, [{ ok: false, status: 403, data: { detail: "Only organisers and coordinators can view event requests" } }]);
  await state.fetchEvents();
  assert.equal(state.eventsView.denied, true);
  assert.equal(state.eventsView.error, "Only organisers and coordinators can view event requests");
  assert.ok(!state.alert?.message, "the error is shown on the page, not as a second alert");

  recorder(state, [{ ok: true, status: 200, data: [row(1, "Draft")] }, { ok: false, status: 0, data: { detail: "Cannot reach the backend service." } }]);
  await state.fetchEvents();
  assert.deepEqual(plain(state.eventsView), { loaded: true, error: "", denied: false, query: "", group: "all", sort: "start", busyId: null });
  await state.fetchEvents();
  assert.equal(state.events.length, 1, "a failed refresh keeps the list");
  assert.equal(state.eventsView.error, "Cannot reach the backend service.");

  resetSignedOutState(state);
  assert.equal(state.eventsView.loaded, false);
  for (const label of ["Loading your requests…", "You can't view these requests", "Couldn't load your requests", "No event requests yet"]) assert.ok(html.includes(label), label);
});

test("TC-9905 rows have no status badge; View details comes first and Submit for review is the only filled button", () => {
  const start = html.indexOf('x-for="event in group.events"');
  const rowHtml = html.slice(start, html.indexOf("</li></template>", start));
  assert.doesNotMatch(rowHtml, /status-badge/, "the section heading shows the status");
  assert.match(rowHtml, /x-if="group\.key === 'other'"><div><dt>Status<\/dt><dd x-text="event\.status">/, "an ungrouped status is still shown");
  const actions = rowHtml.slice(rowHtml.indexOf('class="request-actions'), rowHtml.indexOf("</div>", rowHtml.indexOf('class="request-actions')));
  assert.match(actions, /^class="request-actions button-group">\s*<button type="button" class="button button-secondary" @click="openEventDetail\(event\.id\)">View details/);
  assert.deepEqual([...rowHtml.matchAll(/button-primary/g)].length, 1);
  assert.match(rowHtml, /event\.status === 'Draft'"><button type="button" class="button button-primary"/);
  assert.match(rowHtml, /event\.status === 'Returned'"><button type="button" class="button button-secondary" @click="openAmend\(event\)">Edit request/); // renamed from "Update and resubmit"
  assert.match(rowHtml, /\['Submitted', 'Under Review'\]\.includes\(event\.status\)"><button type="button" class="button button-secondary" @click="openAmend\(event\)">Edit request/); // renamed from "Amend"
  assert.match(rowHtml, /event\.clarification_note \|\| 'No note was included/);
});

test("TC-9906 a new request can be submitted straight away, or saved as a draft", async () => {
  const state = app();
  state.draft.title = "Summit";
  let sent = recorder(state, [{ ok: true, status: 201, data: { id: 101, status: "Draft" } }, { ok: true, status: 200, data: { id: 101, status: "Submitted" } }]);
  await state.createEventDraft(state.draft, { submit: true });
  assert.deepEqual(sent.map(request => `${request.method} ${request.path}`).slice(0, 2), ["POST /events", "POST /events/101/submit"]);
  assert.equal(state.alert.message, "Your event request was submitted for review.");
  assert.equal(state.draft.title, "", "the form is cleared once saved");

  state.draft.title = "Past event";
  sent = recorder(state, [{ ok: true, status: 201, data: { id: 102 } }, { ok: false, status: 400, data: { detail: "Proposed start time cannot be in the past" } }]);
  await state.createEventDraft(state.draft, { submit: true });
  assert.equal(state.currentPath, "/events");
  assert.equal(state.alert.message, "Saved as a draft, but it wasn't submitted: Proposed start time cannot be in the past");

  state.draft.title = "Later";
  sent = recorder(state, [{ ok: true, status: 201, data: { id: 103 } }]);
  await state.createEventDraft(state.draft);
  assert.deepEqual(sent.map(request => request.path).slice(0, 1), ["/events"]);
  assert.ok(!sent.some(request => request.path.endsWith("/submit")));
  assert.equal(state.alert.message, "Your event request was saved as a draft.");
});

test("TC-9907 a Returned request is amended and resubmitted from one dialog", async () => {
  const state = app();
  const returned = { ...row(5, "Returned", { clarification_note: "Which hall?" }), venue_requirements: "", accessibility_needs: "", equipment_requirements: "", registration_needs: "" };
  state.openAmend(returned);
  assert.equal(state.modal.resubmit, true);
  assert.equal(state.amendChangeCount(), 0);

  // No changes: resubmitted as it is, nothing amended
  let sent = recorder(state, () => ({ ok: true, status: 200, data: [] }));
  await state.submitModal();
  assert.deepEqual(sent.map(request => request.path).filter(path => path !== "/events"), ["/events/5/submit"]);
  assert.equal(state.alert.message, "Request resubmitted");
  assert.equal(state.modal.open, false);

  // Changes: amend, then submit
  state.openAmend(returned);
  state.amendForm.venue_requirements = "Hall B";
  assert.equal(state.amendChangeCount(), 1);
  sent = recorder(state, () => ({ ok: true, status: 200, data: [] }));
  await state.submitModal();
  assert.deepEqual(plain(sent.filter(request => request.method === "POST").map(request => [request.path, request.body ?? null])), [["/events/5/amend", { venue_requirements: "Hall B" }], ["/events/5/submit", null]]);

  // Submit refused after the amend saved: the dialog stays open and says what happened
  state.openAmend(returned);
  state.amendForm.title = "Event 5 v2";
  recorder(state, path => path.endsWith("/submit") ? { ok: false, status: 400, data: { detail: "Proposed start time cannot be in the past" } } : { ok: true, status: 200, data: [] });
  await state.submitModal();
  assert.equal(state.modal.open, true);
  assert.equal(state.alert.message, "Your changes were saved, but the request wasn't resubmitted: Proposed start time cannot be in the past");
  assert.equal(state.amendChangeCount(), 0, "the saved title is no longer counted as a change");

  // A request awaiting review is only amended
  state.openAmend({ ...returned, status: "Submitted" });
  assert.equal(state.modal.resubmit, false);
  state.amendForm.title = "New title";
  sent = recorder(state, () => ({ ok: true, status: 200, data: [] }));
  await state.submitModal();
  assert.deepEqual(sent.filter(request => request.method === "POST").map(request => request.path), ["/events/5/amend"]);
  assert.equal(state.alert.message, "Changes saved");
});

test("TC-9908 submitting a draft from the list can't be sent twice", async () => {
  const state = app();
  let release;
  const sent = [];
  state.apiRequest = path => { sent.push(path); return new Promise(done => { release = done; }); };
  const first = state.submitEvent(4);
  await state.submitEvent(4);
  assert.equal(state.eventsView.busyId, 4);
  release({ ok: false, status: 400, data: {} });
  await first;
  assert.deepEqual(sent, ["/events/4/submit"]);
  assert.equal(state.eventsView.busyId, null);
});

// ---- Clarification buttons: Edit request, Save, Resubmit (UI_DECISIONS "Button labels")
const modalActions = html.slice(html.indexOf('<div class="modal-actions"><span class="change-count"'), html.indexOf("</fieldset></form></section></div></template>", html.indexOf('<div class="modal-actions"><span class="change-count"')));

test("TC-9995 the row opens 'Edit request', for Returned and for requests awaiting review alike", () => {
  assert.match(html, /<template x-if="event.status === 'Returned'"><button type="button" class="button button-secondary" @click="openAmend\(event\)">Edit request<\/button><\/template>/);
  assert.match(html, /<template x-if="\['Submitted', 'Under Review'\].includes\(event.status\)"><button type="button" class="button button-secondary" @click="openAmend\(event\)">Edit request<\/button><\/template>/);
  assert.match(html, /x-text="modal.kind === 'reject' \? 'Reject request' : modal.kind === 'return' \? 'Request clarification' : 'Edit request'"/);
  for (const old of ["Update and resubmit", "Save without resubmitting", "Save and resubmit", "Resubmit as it is", "'Save changes') :", ">Amend<"]) assert.ok(!html.includes(old), old);
});

test("TC-9996 no changes: Cancel and Resubmit; changes: Cancel, Save and Resubmit, with Resubmit the only filled button", () => {
  // Save shows only with unsaved changes on a Returned request, and is secondary
  assert.match(modalActions, /<button type="button" @click="closeModal\(\)" class="button button-secondary">Cancel<\/button><template x-if="modal.kind === 'amend' && modal.resubmit && amendChangeCount\(\)"><button type="button" class="button button-secondary"/);
  assert.match(modalActions, /'Saving…' : 'Save'/);
  assert.match(modalActions, /modal.resubmit \? \(decisionBusy && modal.action === 'resubmit' \? 'Resubmitting…' : 'Resubmit'\)/);
  assert.equal((modalActions.match(/button-primary/g) || []).length, 1, "one filled button");
  const order = ["closeModal()", "amendEvent(modal.event.id, amendForm)", "'Resubmit'"].map(text => modalActions.indexOf(text));
  assert.deepEqual([...order].sort((a, b) => a - b), order, "Cancel, Save, Resubmit");
});

test("TC-9997 Save keeps the request Returned; Resubmit saves then submits; the running button says so", async () => {
  const state = app();
  const returned = { ...row(6, "Returned", { clarification_note: "Which hall?" }), venue_requirements: "", accessibility_needs: "", equipment_requirements: "", registration_needs: "" };
  // Save: only POST /amend
  state.openAmend(returned);
  state.amendForm.venue_requirements = "Hall A";
  let during;
  let sent = [];
  state.apiRequest = async (path, options = {}) => { sent.push([options.method || "GET", path]); if (options.method === "POST") during = [state.decisionBusy, state.modal.action]; return { ok: true, status: 200, data: [] }; };
  await state.amendEvent(6, state.amendForm);
  assert.deepEqual(sent.filter(([method]) => method === "POST"), [["POST", "/events/6/amend"]], "Save never calls /submit");
  assert.deepEqual(during, [true, "save"]);
  assert.equal(state.alert.message, "Changes saved");
  assert.equal(state.modal.open, false);

  // Resubmit with changes: amend, then submit
  state.openAmend(returned);
  state.amendForm.title = "Event 6 v2";
  sent = [];
  const actions = [];
  state.apiRequest = async (path, options = {}) => { sent.push([options.method || "GET", path]); if (options.method === "POST") actions.push(state.modal.action); return { ok: true, status: 200, data: [] }; };
  await state.submitModal();
  assert.deepEqual(sent.filter(([method]) => method === "POST").map(([, path]) => path), ["/events/6/amend", "/events/6/submit"]);
  assert.deepEqual(actions, ["resubmit", "resubmit"]);
  assert.equal(state.alert.message, "Request resubmitted");

  // Cancel: closes, sends nothing
  state.openAmend(returned);
  state.amendForm.title = "Not kept";
  sent = [];
  state.closeModal();
  assert.equal(state.modal.open, false);
  assert.deepEqual(sent, []);
  state.openAmend(returned);
  assert.equal(state.amendForm.title, "Event 6", "reopening starts from the saved values");
  assert.equal(state.amendChangeCount(), 0);
});
