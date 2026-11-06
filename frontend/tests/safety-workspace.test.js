import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { safetyWorkspace } from "../src/safety-workspace.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");

const LIST = "GET /safety/events";
const DETAIL = "GET /safety/events/5";
const DECIDE = "POST /safety/events/5/decision";
const row = { id: 5, title: "Annual Gala", status: "Safety Review", proposed_start: "2099-03-10T10:00:00", expected_attendance: 80, coordinator: "coord1", venues: ["Grand Hall"] };
const detail = {
  ...row,
  accessibility_needs: "Two wheelchair users",
  venue_requirements: "Theatre layout",
  equipment_requirements: null,
  venue_details: [{ booking_id: 1, booking_start: "2099-03-10T02:00:00", booking_end: "2099-03-10T06:00:00", booking_status: "Confirmed", name: "Grand Hall", location: "Block A", capacity: 60, supported_layouts: ["Theatre"], facilities: ["Stage"], accessibility: "Step-free", operating_hours: "08:00-22:00", setup_time_mins: 30, turnaround_time_mins: 45, unavailable_periods: [{ start_at: "2099-03-10T06:00:00", end_at: "2099-03-10T07:00:00", reason: "Fire door repair" }] }],
  equipment: [{ id: 1, equipment_type: "Projector", quantity: 2, technical_requirements: "Left of the stage", status: "arranged", status_note: "Checked on site" }],
  checks: [{ id: 1, decision: "request_changes", reason: "Move the stage", decided_by: "safety1", decided_at: "2099-01-01T00:00:00" }],
};

function workspace(replies = {}) {
  const sent = [];
  return Object.assign(safetyWorkspace(), {
    sent,
    apiRequest: async (path, options = {}) => {
      sent.push({ path, method: options.method || "GET", body: options.body, inlineErrors: options.inlineErrors });
      const reply = replies[`${options.method || "GET"} ${path}`];
      return typeof reply === "function" ? reply() : reply || { ok: false, status: 500, data: { detail: "Unexpected request" } };
    },
  });
}

const ok = data => ({ ok: true, status: 200, data });

test("TC-8622 the workspace lists the events waiting for a check", async () => {
  const state = workspace({ [LIST]: ok([row]) });
  await state.init();
  assert.equal(state.safety.loaded, true);
  assert.deepEqual(state.safety.items.map(e => e.title), ["Annual Gala"]);
  assert.equal(state.sent[0].inlineErrors, true);
});

test("TC-8623 an empty list and a failed list each read as what they are", async () => {
  const empty = workspace({ [LIST]: ok([]) });
  await empty.init();
  assert.equal(empty.safety.items.length, 0);
  assert.equal(empty.safety.error, "");

  const failed = workspace({ [LIST]: { ok: false, status: 500, data: { detail: "Try later" } } });
  await failed.init();
  assert.equal(failed.safety.error, "Try later");
  assert.equal(failed.safety.loaded, false);
});

test("TC-8624 opening an event loads everything needed to decide, and warns when attendance is over capacity", async () => {
  const state = workspace({ [LIST]: ok([row]), [DETAIL]: ok(detail) });
  await state.init();
  await state.openSafetyEvent(5);
  const open = state.safety.selected;
  assert.equal(open.expected_attendance, 80);
  assert.equal(open.accessibility_needs, "Two wheelchair users");
  assert.equal(open.venue_details[0].capacity, 60);
  assert.equal(open.venue_details[0].unavailable_periods[0].reason, "Fire door repair");
  assert.equal(open.equipment[0].technical_requirements, "Left of the stage");
  assert.equal(state.overCapacity(open, open.venue_details[0]), true);
  assert.equal(state.overCapacity(open, { capacity: 100 }), false);
  assert.equal(state.decisionLabel("request_changes"), "Changes requested");
  assert.equal(state.listOrNone(open.venue_details[0].supported_layouts), "Theatre");
  assert.equal(state.listOrNone([]), "Not recorded");
});

test("TC-8625 approving needs no reason; the event leaves the list with a notice", async () => {
  let listed = [row];
  const state = workspace({
    [LIST]: () => ok(listed),
    [DETAIL]: ok(detail),
    [DECIDE]: () => { listed = []; return ok({ status: "Confirmed" }); },
  });
  await state.init();
  await state.openSafetyEvent(5);
  await state.submitSafetyDecision("approve");
  assert.deepEqual(state.sent.find(r => r.method === "POST").body, { decision: "approve", reason: null });
  assert.equal(state.safety.selected, null);
  assert.equal(state.safety.items.length, 0);
  assert.match(state.safety.notice, /Annual Gala.*Confirmed/);
});

for (const [decision, outcome] of [["reject", /rejected/], ["request_changes", /Changes were requested/]]) {
  test(`TC-8626 ${decision} needs a reason before anything is sent, then returns the event to Planning`, async () => {
    const state = workspace({ [LIST]: ok([row]), [DETAIL]: ok(detail), [DECIDE]: ok({ status: "Planning" }) });
    await state.init();
    await state.openSafetyEvent(5);
    state.safety.reason = "   ";
    await state.submitSafetyDecision(decision);
    assert.match(state.safety.reasonError, /reason/i);
    assert.equal(state.sent.filter(r => r.method === "POST").length, 0);

    state.safety.reason = " Exits blocked ";
    await state.submitSafetyDecision(decision);
    assert.deepEqual(state.sent.find(r => r.method === "POST").body, { decision, reason: "Exits blocked" });
    assert.match(state.safety.notice, outcome);
    assert.match(state.safety.notice, /Planning/);
  });
}

test("TC-8627 a refused decision keeps the form and what was typed, and an event already decided reloads the list", async () => {
  const refused = workspace({ [LIST]: ok([row]), [DETAIL]: ok(detail), [DECIDE]: { ok: false, status: 422, data: { detail: [{ loc: ["body"], msg: "Value error, A reason is required when rejecting or requesting changes" }] } } });
  await refused.init();
  await refused.openSafetyEvent(5);
  refused.safety.reason = "Exits blocked";
  await refused.submitSafetyDecision("reject");
  assert.equal(refused.safety.formError, "A reason is required when rejecting or requesting changes");
  assert.equal(refused.safety.reason, "Exits blocked");
  assert.equal(refused.safety.selected.id, 5);
  assert.equal(refused.safety.busy, false);

  const gone = workspace({ [LIST]: ok([row]), [DETAIL]: ok(detail), [DECIDE]: { ok: false, status: 404, data: { detail: "This event isn't waiting for a safety check" } } });
  await gone.init();
  await gone.openSafetyEvent(5);
  await gone.submitSafetyDecision("approve");
  assert.equal(gone.safety.selected, null);
  assert.equal(gone.safety.error, "This event isn't waiting for a safety check");
  assert.equal(gone.sent.filter(r => r.path === "/safety/events").length, 2);
});

test("TC-8628 details that can't be loaded say so and leave the list usable", async () => {
  const state = workspace({ [LIST]: ok([row]), [DETAIL]: { ok: false, status: 404, data: { detail: "This event isn't waiting for a safety check" } } });
  await state.init();
  await state.openSafetyEvent(5);
  assert.equal(state.safety.selected, null);
  assert.equal(state.safety.detailError, "This event isn't waiting for a safety check");
  assert.equal(state.safety.items.length, 1);
});

test("TC-8629 the markup: the workspace sits at its fixed place behind its role, with every field the customer listed", () => {
  const at = html.indexOf("SCRUM-86 Safety Officer workspace");
  const panel = html.slice(at, html.indexOf("SCRUM-41 equipment catalogue workspace"));
  assert.match(panel, /x-if="currentPath === '\/safety' && user\.role === 'Safety Officer'"/);
  assert.match(panel, /x-data="safetyWorkspace\(\)"/);
  for (const field of ["expected_attendance", "capacity", "supported_layouts", "accessibility_needs", "technical_requirements", "unavailable_periods", "operating_hours"]) {
    assert.ok(panel.includes(field), field);
  }
  assert.match(panel, /<label for="safety-reason">/);
  for (const decision of ["approve", "request_changes", "reject"]) assert.ok(panel.includes(`submitSafetyDecision('${decision}')`), decision);
  assert.match(panel, /role="status"/);
  assert.match(panel, /role="alert"/);
  assert.match(main, /import \{ safetyWorkspace \} from "\.\/safety-workspace\.js";/);
  assert.match(main, /Alpine\.data\("safetyWorkspace", safetyWorkspace\);/);
});


test("TC-8631 Refresh reads the list again, so a new event appears and a cancelled one goes", async () => {
  let items = [row];
  const state = workspace({ [LIST]: () => ok(items) });
  await state.init();
  assert.equal(state.safety.items.length, 1);
  items = [{ ...row, id: 6, title: "Spring Fair" }];
  await state.refreshSafetyEvents();
  assert.deepEqual(state.safety.items.map(item => item.id), [6]);
  assert.match(html, /<button type="button"[^>]*:disabled="safety\.loading"[^>]*@click="refreshSafetyEvents\(\)"[^>]*>[\s\S]{0,80}Refresh<\/button>/);

  const failing = workspace({ [LIST]: () => ok([row]) });
  await failing.init();
  failing.safety.error = "Old failure";
  await failing.refreshSafetyEvents();
  assert.equal(failing.safety.error, "");
});
