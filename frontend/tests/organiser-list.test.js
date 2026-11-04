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

test("TC-9901 requests are grouped by what they need from the Organiser, most urgent first", () => {
  const state = app();
  state.events = [row(1, "Approved"), row(2, "Draft"), row(3, "Rejected"), row(4, "Returned"), row(5, "Under Review"), row(6, "Submitted"), row(7, "Mystery")];
  const groups = plain(state.organiserGroups());
  assert.deepEqual(groups.map(group => group.key), ["respond", "draft", "review", "active", "closed", "other"]);
  assert.deepEqual(groups.map(group => group.events.map(event => event.id)), [[4], [2], [5, 6], [1], [3], [7]]);
  // Every backend status lands in a named group
  const statuses = [...readFileSync(new URL("../../backend/status.py", import.meta.url), "utf8").matchAll(/=\s*"([^"]+)"/g)].map(match => match[1]);
  for (const status of statuses) assert.notEqual(state.organiserGroupOf({ status }), "other", status);
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
  state.eventsView.group = "review";
  assert.deepEqual(ids(), [2, 1, 3]);
  assert.deepEqual(plain(state.organiserFilterChips()), [
    { key: "all", label: "All", count: 4 }, { key: "draft", label: "Drafts", count: 1 }, { key: "review", label: "Awaiting review", count: 3 },
  ], "only groups with requests get a chip");
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
  for (const label of ["Loading your requests…", "You can't view these requests", "Couldn't load your requests", "No event requests yet", "No requests match"]) assert.ok(html.includes(label), label);
});

test("TC-9905 each request has one primary action, chosen by its status", () => {
  const start = html.indexOf('x-for="event in group.events"');
  const rowHtml = html.slice(start, html.indexOf("</li></template>", start));
  const primaries = [...rowHtml.matchAll(/<template x-if="event\.status === '(\w+)'"><button type="button" class="button button-primary"[^>]*>/g)].map(match => match[1]);
  assert.deepEqual(primaries, ["Returned", "Draft"], "only requests that need the Organiser get a primary button");
  assert.match(rowHtml, /Update and resubmit/);
  assert.match(rowHtml, /\['Submitted', 'Under Review'\]\.includes\(event\.status\)"><button type="button" class="button button-secondary" @click="openAmend\(event\)">Amend/);
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
  assert.equal(state.alert.message, "Your request was resubmitted for review.");
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
  assert.equal(state.alert.message, "Event request updated.");
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
