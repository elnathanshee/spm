import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { resetSignedOutState } from "../src/session-state.js";
import { normalizeRole } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
function app() {
  let factory;
  let poll;
  const context = {
    NAVIGATION: [],
    normalizeRoleName: normalizeRole,
    requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() },
    Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: { hash: "" }, setInterval: (fn, delay) => { poll = { fn, delay }; } },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  return { state: factory(), getPoll: () => poll };
}

test("TC-6421 fetch retains every complete message and polling refreshes notifications", async () => {
  const { state, getPoll } = app();
  const messages = [{ id: 1, message: "reason ".repeat(500) }, { id: 2, message: "<script>text</script>" }];
  state.user = { role: "Venue Staff" };
  state.apiRequest = async path => { assert.equal(path, "/notifications"); return { ok: true, data: messages }; };
  state.loadSession = async () => {};
  await state.init();
  assert.equal(getPoll().delay, 15000);
  await getPoll().fn();
  assert.equal(state.notifications, messages);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /class="notification-center"/);
  assert.match(html, /class="notification-panel"/);
  assert.match(html, /x-for="notification in notifications"/);
  assert.match(html, /x-text="formatNotificationMessage\(notification\)"/);
  assert.match(html, /unreadNotifications\(\)/);
});

test("TC-6422 booking notification kinds receive clear semantic titles and tones", () => {
  const { state } = app();
  assert.equal(state.notificationTitle({ kind: "booking_submitted" }), "New booking request");
  assert.equal(state.notificationTitle({ kind: "booking_approved" }), "Booking approved");
  assert.equal(state.notificationTitle({ kind: "booking_rejected" }), "Booking rejected");
  assert.equal(state.notificationTone({ kind: "booking_approved" }), "success");
  assert.equal(state.notificationTone({ kind: "booking_rejected" }), "warning");
  assert.equal(state.notificationTone({ kind: "booking_submitted" }), "action");
});

test("TC-6423 booking notification timestamps are presented as readable dates", () => {
  const { state } = app();
  const message = state.formatNotificationMessage({ message: 'Booking from 2026-10-19 16:38:00 to 2026-10-29T16:38:00 was submitted.' });
  assert.doesNotMatch(message, /2026-10-19 16:38:00|2026-10-29T16:38:00/);
  assert.match(message, /2026/);
});

test("TC-6424 late responses cannot restore notifications after logout or account switch", async () => {
  const { state } = app();
  state.user = { user_id: 1 };
  let resolve;
  state.apiRequest = () => new Promise(done => { resolve = done; });
  let pending = state.fetchNotifications();
  resetSignedOutState(state);
  resolve({ ok: true, data: [{ id: 1, message: "private" }] });
  await pending;
  assert.deepEqual(state.notifications, []);
  state.user = { user_id: 1 };
  pending = state.fetchNotifications();
  state.user = { user_id: 2 };
  resolve({ ok: true, data: [{ id: 1 }] });
  await pending;
  assert.deepEqual(state.notifications, []);
});

test("TC-9512 hold notifications retain payloads, distinct mappings and read behaviour with null booking IDs", async () => {
  const { state } = app();
  const kinds = ["hold_warning", "hold_expired", "hold_expiry_set"];
  const titles = ["Hold expiry warning", "Hold expired", "Hold expiry updated"];
  const tones = ["action", "warning", "info"];
  const messages = kinds.map((kind, index) => ({ id: index + 1, event_id: 7, booking_id: null, kind, message: `Backend hold message ${index}`, read: false, created_at: "2026-10-28T09:00:00" }));
  state.user = { user_id: 2, role: "Coordinator" };
  const calls = [];
  state.apiRequest = async (path, options) => {
    calls.push(path);
    // Opening the event also loads its equipment (Coordinator and Lead request details)
    if (path.endsWith("/equipment-requests")) return { ok: true, data: { equipment_requests: [] } };
    if (!options) return { ok: true, data: messages };
    assert.equal(options.method, "POST");
    return { ok: true, data: { ...messages[0], read: true } };
  };
  await state.fetchNotifications();
  assert.equal(state.unreadNotifications(), 3);
  messages.forEach((notification, index) => {
    assert.equal(state.notificationTitle(notification), titles[index]);
    assert.equal(state.notificationTone(notification), tones[index]);
    assert.equal(state.formatNotificationMessage(notification), notification.message);
  });
  state.notificationOpen = true;
  await state.openNotification(messages[0]);
  assert.equal(state.unreadNotifications(), 2);
  // It opens the event the notification is about, and closes the panel
  assert.equal(state.notificationOpen, false);
  assert.equal(state.eventDetail.id, 7);
  state.closeEventDetail();
  await state.openNotification(state.notifications[0]);
  assert.deepEqual(calls.filter(path => path.startsWith("/notifications/")), ["/notifications/1/read"]);
  assert.equal(state.notificationTitle({ kind: "unknown" }), "Workspace update");
});

test("TC-9811 a notification links only to a record its backend links and the user's role allow", () => {
  const { state } = app();
  const booking = { id: 1, event_id: 4, booking_id: 9, kind: "booking_submitted" };
  const event = { id: 2, event_id: 4, booking_id: null, kind: "approved" };
  const reassignedAway = { id: 3, event_id: null, booking_id: null, kind: "reassigned_away" };
  state.user = { role: "Venue Staff" };
  assert.deepEqual(JSON.parse(JSON.stringify(state.notificationTarget(booking))), { kind: "booking", id: 9, label: "Open booking" });
  assert.equal(state.notificationTarget(event), null, "Venue Staff can't read GET /events/{id}");
  for (const role of ["Organiser", "Coordinator", "Event Coordinator Lead"]) {
    state.user = { role };
    assert.equal(state.notificationTarget(event).kind, "event", role);
    assert.equal(state.notificationTarget({ ...booking, kind: "booking_approved" }).id, 4, `${role} opens the booked event`);
    assert.equal(state.notificationTarget(reassignedAway), null, "no link to an event the user lost access to");
  }
  for (const role of ["Technical Support", "Attendee"]) {
    state.user = { role };
    assert.equal(state.notificationTarget(event), null, role);
  }
});
