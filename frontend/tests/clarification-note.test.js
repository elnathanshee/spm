import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function app(role) {
  let factory;
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  });
  const state = factory();
  state.user = { user_id: 1, role: normalizeRole(role) };
  return state;
}

// Exactly what backend events.amend_event writes, and a real POST /return note
const amended = "Request amended by organiser. Fields changed: equipment_requirements";
const question = "Which hall do you need, and how many guests use wheelchairs?";

test("TC-9998 a Coordinator's question and the automatic amendment note read as what they are, for every role", () => {
  const organiser = app("organiser");
  assert.deepEqual(plain(organiser.clarificationNote({ status: "Returned", clarification_note: question })), { kind: "question", heading: "Coordinator's clarification request", text: question, detail: "" });
  const update = plain(organiser.clarificationNote({ status: "Returned", clarification_note: amended }));
  assert.deepEqual([update.kind, update.heading, update.text], ["update", "Latest update", "You updated Equipment"]);
  assert.match(update.detail, /Coordinator's question can't be shown here after an update/);
  for (const role of ["coordinator", "lead"]) {
    const note = plain(app(role).clarificationNote({ status: "Returned", clarification_note: amended }));
    assert.deepEqual([note.kind, note.heading, note.text], ["update", "Latest update", "Organiser updated Equipment"], role);
  }
  // Never labelled as the Coordinator's
  for (const role of ["organiser", "coordinator", "lead"]) assert.notEqual(app(role).clarificationNote({ status: "Returned", clarification_note: amended }).heading, "Coordinator's clarification request");
});

test("TC-9999 every amendable field has its form label; unknown fields read as words; several are listed", () => {
  const state = app("organiser");
  const fields = ["title", "purpose", "description", "proposed_start", "expected_attendance", "venue_requirements", "accessibility_needs", "equipment_requirements", "registration_needs"];
  assert.deepEqual(fields.map(field => state.requestFieldLabel(field)), ["Event title", "Purpose", "Description", "Proposed start", "Expected attendance", "Venue", "Accessibility", "Equipment", "Registration and check-in"]);
  // The labels match the edit form
  for (const [id, label] of [["amend-venue-requirements", "Venue"], ["amend-equipment-requirements", "Equipment"], ["amend-registration-needs", "Registration and check-in"], ["amend-accessibility-needs", "Accessibility"]]) assert.match(html, new RegExp(`for="${id}">${label}<`));
  assert.equal(state.requestFieldLabel("catering_needs"), "Catering needs");
  assert.equal(state.requestFieldLabel(""), "Another field");
  assert.equal(state.clarificationNote({ status: "Returned", clarification_note: "Request amended by organiser. Fields changed: venue_requirements, equipment_requirements, catering_needs" }).text, "You updated Venue, Equipment and Catering needs");
  assert.equal(state.clarificationNote({ status: "Returned", clarification_note: "Request amended by organiser. No field values changed." }).text, "You saved the request without changing any fields");
});

test("TC-10000 empty and earlier notes: the fallback for a Returned request, and a past question outside Returned", () => {
  const state = app("coordinator");
  assert.equal(state.clarificationNote({ status: "Returned", clarification_note: null }).text, "No note was included with this request.");
  assert.equal(state.clarificationNote({ status: "Approved", clarification_note: null }), null);
  assert.equal(state.clarificationNote({ status: "Approved", clarification_note: question }).detail, "Sent when the request was last returned for clarification.");
  assert.equal(state.clarificationNote(null), null);
});

test("TC-10001 one shared note block on the Organiser list, the edit dialog and the request details", () => {
  const blocks = [...html.matchAll(/<template x-if="clarificationNote\(([^)]+)\)"><div class="callout clarification-note" :class="clarificationNote\(\1\)\.kind === 'question' \? 'callout-attention' : 'callout-neutral'">/g)].map(match => match[1]);
  assert.deepEqual(blocks, ["event", "eventDetail.detail", "modal.event"]);
  assert.doesNotMatch(html, /Latest clarification note|<strong>Coordinator's clarification request<\/strong>/, "no hard-coded heading is left");
  // The details grid stays right after the badge (UI-D1); the note follows the grid
  assert.match(html, /x-text="eventDetail.detail.status"><\/span><dl class="booking-detail-grid event-detail-fields">/);
  assert.match(html, /<\/dl><template x-if="clarificationNote\(eventDetail.detail\)">/);
});

test("TC-10002 request details label fields like the form", () => {
  const state = app("coordinator");
  assert.deepEqual(["equipment_requirements", "venue_requirements", "proposed_start", "title", "organiser", "assigned_coordinator", "some_new_field"].map(key => state.eventFieldLabel(key)),
    ["Equipment", "Venue", "Proposed start", "Event title", "Organiser", "Assigned Coordinator", "Some new field"]);
});
