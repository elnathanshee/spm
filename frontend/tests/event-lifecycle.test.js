import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { eventLifecycle } from "../src/event-lifecycle.js";
import { reviewFlags } from "../src/review-flags.js";
import { NAVIGATION, canAccess, defaultPathForRole, normalizeRole, resolveRolePath } from "../src/rbac.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

function appState(role) {
  let factory;
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  });
  const state = factory();
  state.user = { user_id: 7, role: normalizeRole(role) };
  return state;
}

// The parent scope a component finds through `this` in the real page.
function withParent(component, { role = "Coordinator", replies = {}, detail = {} } = {}) {
  const sent = [];
  const refreshed = [];
  return Object.assign(component, {
    sent,
    refreshed,
    user: { user_id: 7, role },
    normalizeRole,
    eventDetail: { id: 5, detail: { id: 5, status: "Planning", organiser_id: 1, assigned_coordinator_id: 7, ...detail } },
    fetchEvents: async () => refreshed.push("events"),
    fetchAssignedEvents: async () => refreshed.push("assigned"),
    fetchLeadData: async () => refreshed.push("lead"),
    apiRequest: async (path, options = {}) => {
      sent.push({ path, method: options.method || "GET", body: options.body });
      const reply = replies[`${options.method || "GET"} ${path}`];
      return reply || { ok: false, status: 500, data: { detail: "Unexpected request" } };
    },
  });
}

const ACTIONS = "GET /events/5/lifecycle-actions";
const offered = (...actions) => ({ ok: true, status: 200, data: actions });
const action = (name, label, why = null) => ({ action: name, label, available: why === null, why });

test("TC-10242 the panel loads what this person may do, and says why a button is unavailable", async () => {
  const state = withParent(eventLifecycle(), { replies: { [ACTIONS]: offered(action("submit-safety", "Submit for safety check", "It needs at least one Confirmed venue booking."), action("cancel", "Cancel event")) } });
  await state.init();
  assert.equal(state.lifecycle.loaded, true);
  const [submit, cancel] = state.lifecycle.actions;
  assert.equal(submit.available, false);
  assert.match(submit.why, /Confirmed venue booking/);
  assert.equal(cancel.available, true);
  assert.equal(state.lifecycleUnavailableReasons().length, 1);
});

test("TC-10243 an available action posts, updates the status shown and refreshes the Coordinator's list", async () => {
  const state = withParent(eventLifecycle(), {
    detail: { status: "Approved" },
    replies: {
      [ACTIONS]: offered(action("start-planning", "Start planning")),
      "POST /events/5/start-planning": { ok: true, status: 200, data: { status: "Planning", message: "Planning started" } },
    },
  });
  await state.init();
  await state.runLifecycleAction(state.lifecycle.actions[0]);
  assert.equal(state.eventDetail.detail.status, "Planning");
  assert.equal(state.lifecycle.notice, "Planning started");
  assert.ok(state.refreshed.includes("assigned"));
  assert.equal(state.sent.filter(r => r.path === "/events/5/lifecycle-actions").length, 2, "the actions are read again for the new status");
});

test("TC-10244 an unavailable action sends nothing", async () => {
  const state = withParent(eventLifecycle(), { replies: { [ACTIONS]: offered(action("complete", "Mark completed", "This event hasn't ended yet.")) } });
  await state.init();
  await state.runLifecycleAction(state.lifecycle.actions[0]);
  assert.deepEqual(state.sent.map(r => r.method), ["GET"]);
});

test("TC-10245 a server refusal is shown in the panel and the status stays", async () => {
  const state = withParent(eventLifecycle(), {
    replies: {
      [ACTIONS]: offered(action("submit-safety", "Submit for safety check")),
      "POST /events/5/submit-safety": { ok: false, status: 409, data: { detail: "This event can't go to the safety check yet." } },
    },
  });
  await state.init();
  await state.runLifecycleAction(state.lifecycle.actions[0]);
  assert.equal(state.lifecycle.error, "This event can't go to the safety check yet.");
  assert.equal(state.eventDetail.detail.status, "Planning");
  assert.equal(state.lifecycle.busy, false);
});

test("TC-10246 cancelling asks for a reason first and sends it", async () => {
  const state = withParent(eventLifecycle(), {
    role: "Event Coordinator Lead",
    replies: {
      [ACTIONS]: offered(action("cancel", "Cancel event")),
      "POST /events/5/cancel": { ok: true, status: 200, data: { status: "Cancelled", message: "Event cancelled" } },
    },
  });
  await state.init();
  await state.runLifecycleAction(state.lifecycle.actions[0]);
  assert.equal(state.lifecycle.cancelling, true, "the first click opens the reason form");
  assert.equal(state.sent.filter(r => r.method === "POST").length, 0);

  state.lifecycle.reason = "   ";
  await state.submitLifecycleCancel();
  assert.match(state.lifecycle.reasonError, /reason/i);
  assert.equal(state.sent.filter(r => r.method === "POST").length, 0, "a blank reason is never sent");

  state.lifecycle.reason = " Budget cut ";
  await state.submitLifecycleCancel();
  assert.deepEqual(state.sent.find(r => r.method === "POST").body, { reason: "Budget cut" });
  assert.equal(state.eventDetail.detail.status, "Cancelled");
  assert.equal(state.lifecycle.cancelling, false);
  assert.ok(state.refreshed.includes("lead"));
});

test("TC-10247 a failed load says so, and an Organiser's list refreshes after their cancel", async () => {
  const failed = withParent(eventLifecycle(), { replies: { [ACTIONS]: { ok: false, status: 500, data: { detail: "Try later" } } } });
  await failed.init();
  assert.equal(failed.lifecycle.error, "Try later");
  assert.equal(failed.lifecycle.actions.length, 0);

  const organiser = withParent(eventLifecycle(), {
    role: "Organiser", detail: { status: "Draft", organiser_id: 7 },
    replies: { [ACTIONS]: offered(action("cancel", "Cancel event")), "POST /events/5/cancel": { ok: true, status: 200, data: { status: "Cancelled", message: "Event cancelled" } } },
  });
  await organiser.init();
  organiser.lifecycle.cancelling = true;
  organiser.lifecycle.reason = "Not needed";
  await organiser.submitLifecycleCancel();
  assert.deepEqual([...organiser.refreshed], ["events"]);
});

test("TC-10248 the markup: the panel sits at its fixed place, with labels and a live status", () => {
  const panelAt = html.indexOf("SCRUM-102 lifecycle actions panel");
  const panel = html.slice(panelAt, html.indexOf("SCRUM-87 arrangement review flags panel"));
  assert.match(panel, /x-data="eventLifecycle\(\)"/);
  assert.match(panel, /x-text="item\.why"/);
  assert.match(panel, /:disabled="!item\.available \|\| lifecycle\.busy"/);
  assert.match(panel, /<label for="lifecycle-cancel-reason">/);
  assert.match(panel, /role="status"/);
  assert.match(panel, /role="alert"/);
  assert.match(main, /import \{ eventLifecycle \} from "\.\/event-lifecycle\.js";/);
  assert.match(main, /Alpine\.data\("eventLifecycle", eventLifecycle\);/);
});

test("TC-10250 closing the dialog while a change is saving still refreshes the list and breaks nothing", async () => {
  const state = withParent(eventLifecycle(), { replies: { [ACTIONS]: offered(action("start-planning", "Start planning")) } });
  await state.init();
  const realRequest = state.apiRequest;
  state.apiRequest = async (path, options = {}) => {
    if (options.method !== "POST") return realRequest(path, options);
    state.eventDetail = null;
    return { ok: true, status: 200, data: { status: "Planning", message: "Planning started" } };
  };
  await state.runLifecycleAction(state.lifecycle.actions[0]);
  assert.ok(state.refreshed.includes("assigned"));
  assert.equal(state.lifecycle.busy, false);
  const flags = withParent(reviewFlags());
  flags.eventDetail = null;
  await flags.loadReviewFlags();
  assert.equal(flags.sent.length, 0);
});

test("TC-10249 Safety Review events can have their routine details edited", () => {
  assert.equal(appState("organiser").canEditRoutine({ status: "Safety Review", organiser_id: 7 }), true);
  assert.equal(appState("coordinator").canEditRoutine({ status: "Safety Review", assigned_coordinator_id: 7 }), true);
});

test("TC-6606 the bell names the new notices", () => {
  const state = appState("organiser");
  assert.equal(state.notificationTitle({ kind: "event_confirmed" }), "Event confirmed");
  assert.equal(state.notificationTitle({ kind: "event_cancelled" }), "Event cancelled");
  assert.equal(state.notificationTitle({ kind: "safety_returned" }), "Safety check returned");
  assert.equal(state.notificationTone({ kind: "event_confirmed" }), "success");
  assert.equal(state.notificationTone({ kind: "event_cancelled" }), "warning");
  assert.equal(state.notificationTone({ kind: "safety_returned" }), "warning");
});

// --- SCRUM-86: the role in the app ---

test("TC-8621 the Safety Officer role: both spellings, its own page, and nobody else's", () => {
  for (const spelling of ["safety officer", "safety_officer", "Safety Officer"]) assert.equal(normalizeRole(spelling), "Safety Officer");
  assert.equal(defaultPathForRole("safety_officer"), "/safety");
  const item = NAVIGATION.find(entry => entry.path === "/safety");
  assert.deepEqual([item.label, item.role], ["Safety checks", "Safety Officer"]);
  assert.equal(resolveRolePath("/safety", "safety_officer").accessDenied, false);
  for (const role of ["Organiser", "Coordinator", "Event Coordinator Lead", "Venue Staff", "Technical Support", "Attendee"]) {
    assert.equal(canAccess("Safety Officer", role), false, role);
    assert.equal(resolveRolePath("/safety", role).accessDenied, true, role);
  }
  assert.equal(resolveRolePath("/technical", "safety_officer").accessDenied, true);
});

// --- SCRUM-87: review flags in the event detail dialog ---

const FLAGS = "GET /events/5/review-flags";
const flag = (id, kind, label) => ({ id, kind, label, source: "safety_review", reason: "Exits blocked", created_by: "safety1", created_at: "2099-01-01T00:00:00" });

test("TC-8711 the panel lists what's flagged, for the people who may see it", async () => {
  const state = withParent(reviewFlags(), { replies: { [FLAGS]: { ok: true, status: 200, data: [flag(1, "venue", "Grand Hall"), flag(2, "equipment", "Projector (quantity 2)")] } } });
  await state.init();
  assert.deepEqual(state.flags.items.map(f => f.label), ["Grand Hall", "Projector (quantity 2)"]);
  assert.equal(state.flagKindLabel(state.flags.items[0]), "Venue");
  assert.equal(state.flagKindLabel(state.flags.items[1]), "Equipment");
  assert.equal(state.flagSourceLabel(state.flags.items[0]), "Safety check");
  assert.equal(state.flagSourceLabel({ source: "important_change" }), "Approved change");
});

test("TC-8712 a Coordinator not assigned to the event, and other roles, never ask", async () => {
  for (const [role, detail] of [["Coordinator", { assigned_coordinator_id: 99 }], ["Organiser", { organiser_id: 99 }], ["Venue Staff", {}]]) {
    const state = withParent(reviewFlags(), { role, detail });
    await state.init();
    assert.equal(state.sent.length, 0, role);
    assert.equal(state.flags.items.length, 0, role);
  }
});

test("TC-8713 the assigned Coordinator and the Lead can clear; the Organiser can only read", async () => {
  const coordinator = withParent(reviewFlags());
  const lead = withParent(reviewFlags(), { role: "Event Coordinator Lead", detail: { assigned_coordinator_id: 99 } });
  const organiser = withParent(reviewFlags(), { role: "Organiser", detail: { organiser_id: 7 } });
  assert.equal(coordinator.canClearFlags(), true);
  assert.equal(lead.canClearFlags(), true);
  assert.equal(organiser.canClearFlags(), false);
  assert.equal(organiser.canSeeFlags(), true);
});

test("TC-8714 clearing empties the panel and says so; a failure keeps the list and shows why", async () => {
  const state = withParent(reviewFlags(), {
    replies: {
      [FLAGS]: { ok: true, status: 200, data: [flag(1, "venue", "Grand Hall")] },
      "POST /events/5/review-flags/clear": { ok: true, status: 200, data: { cleared: 1 } },
    },
  });
  await state.init();
  await state.clearReviewFlags();
  assert.equal(state.flags.items.length, 0);
  assert.match(state.flags.notice, /looked at/);

  const failing = withParent(reviewFlags(), {
    replies: {
      [FLAGS]: { ok: true, status: 200, data: [flag(1, "venue", "Grand Hall")] },
      "POST /events/5/review-flags/clear": { ok: false, status: 403, data: { detail: "Only the event's assigned Coordinator or a Lead can clear its review flags" } },
    },
  });
  await failing.init();
  await failing.clearReviewFlags();
  assert.equal(failing.flags.items.length, 1);
  assert.match(failing.flags.error, /assigned Coordinator/);
});

test("TC-8715 the markup: the flags panel sits at its fixed place, shows only when there's something, and reloads with the status", () => {
  const panelAt = html.indexOf("SCRUM-87 arrangement review flags panel");
  const panel = html.slice(panelAt, html.indexOf("SCRUM-44 equipment reservations panel"));
  assert.match(panel, /x-data="reviewFlags\(\)"/);
  assert.match(panel, /\$watch\('eventDetail\?\.detail\?\.status'/);
  assert.match(panel, /x-show="flags\.items\.length \|\| flags\.error \|\| flags\.notice"/);
  assert.match(panel, /x-show="canClearFlags\(\) && flags\.items\.length"/);
  assert.match(main, /Alpine\.data\("reviewFlags", reviewFlags\);/);
});
