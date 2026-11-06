// Sprint 5 equipment on the worklist and in the event details: SCRUM-40 (status and
// note), SCRUM-43 (availability), SCRUM-44 (reserve, release, and the read-only panel).
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { equipmentRequestActions, eventReservations } from "../src/equipment-reservations.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const row = html.slice(html.indexOf('x-data="equipmentRequestActions()"'), html.indexOf("<template x-if=\"currentPath === '/events' && user.role === 'Organiser'\">"));
const panel = html.slice(html.indexOf('x-data="eventReservations()"'), html.indexOf("<!-- [Sprint 5] SCRUM-48 registrations panel"));

const request = () => ({ id: 3, event_id: 7, event_title: "Gala", equipment_type: "Microphone", quantity: 2, latest_status: "requested", latest_note: null });
const available = [
  { item_id: 1, equipment_type: "Audio", name: "Microphone", total_quantity: 10, reserved: 3, unavailable: 2, available: 5 },
  { item_id: 2, equipment_type: "Video", name: "Projector", total_quantity: 1, reserved: 1, unavailable: 0, available: 0 },
];
const held = { id: 9, event_id: 7, item_id: 1, item_name: "Microphone", equipment_type: "Audio", quantity: 2, created_by: "tech", created_at: "2099-01-01T00:00:00", released_at: null, released_by: null };

function actions(replies = {}) {
  const state = equipmentRequestActions();
  const sent = [];
  state.dialogErrorMessage = response => response.data.detail;
  state.formatUtc = value => `utc ${value}`;
  state.apiRequest = async (path, options = {}) => {
    const method = options.method || "GET";
    sent.push([method, path, options.body]);
    const reply = replies[`${method} ${path}`];
    if (reply) return typeof reply === "function" ? reply(options.body) : reply;
    if (path.startsWith("/equipment/availability")) return { ok: true, status: 200, data: { window_start: "2099-03-01T01:00:00", window_end: "2099-03-01T05:00:00", items: available } };
    if (method === "GET") return { ok: true, status: 200, data: { reservations: [held] } };
    return { ok: true, status: 200, data: { reservation: held } };
  };
  return { state, sent };
}

test("TC-4021 the status form starts from the request's latest status, and a save updates the row", async () => {
  const item = request();
  const { state, sent } = actions({
    "POST /equipment-requests/3/status": { ok: true, status: 200, data: { equipment_request: { ...item, latest_status: "reserved", latest_note: "Booked" } } },
  });
  state.startArrangement(item);
  assert.equal(state.arrangement.status, "requested");
  state.arrangement.status = "reserved";
  state.arrangement.note = " Booked ";
  await state.saveArrangementStatus(item);
  assert.deepEqual(sent, [["POST", "/equipment-requests/3/status", { status: "reserved", note: "Booked" }]]);
  assert.equal(item.latest_status, "reserved");
  assert.equal(item.latest_note, "Booked");
  assert.equal(state.arrangement.note, "");
  assert.match(state.arrangement.message, /reserved/);
  assert.equal(state.arrangement.busy, false);
});

test("TC-4022 a refused status update says why and leaves the row and the form as they were", async () => {
  const item = request();
  const { state } = actions({ "POST /equipment-requests/3/status": { ok: false, status: 403, data: { detail: "Only Technical Support Staff can manage the equipment catalogue" } } });
  state.startArrangement(item);
  state.arrangement.status = "arranged";
  state.arrangement.note = "Keep me";
  await state.saveArrangementStatus(item);
  assert.equal(item.latest_status, "requested");
  assert.equal(state.arrangement.note, "Keep me");
  assert.match(state.arrangement.error, /Only Technical Support/);
  assert.equal(state.arrangement.message, "");
});

test("TC-4023 the worklist row shows the latest status and note and offers every status, in labelled fields", () => {
  assert.match(html, /<th scope="col">Arrangements<\/th>/);
  assert.match(html, /<td data-label="Arrangements" class="equipment-cell-arrangements"><div class="equipment-arrangement" x-data="equipmentRequestActions\(\)" x-init="startArrangement\(item\)">/);
  assert.match(row, /x-text="item\.latest_status"/);
  assert.match(row, /x-text="item\.latest_note"/);
  for (const value of ["requested", "reserved", "arranged", "unavailable"]) assert.match(row, new RegExp(`<option value="${value}">`), value);
  assert.match(row, /:for="`equipment-status-\$\{item\.id\}`"/);
  assert.match(row, /:for="`equipment-note-\$\{item\.id\}`"/);
  assert.match(row, /role="status"[^>]*x-text="arrangement\.message"/);
  assert.match(main, /import \{ equipmentRequestActions, eventReservations \} from "\.\/equipment-reservations\.js";/);
  assert.match(main, /Alpine\.data\("equipmentRequestActions", equipmentRequestActions\);/);
});

test("TC-4321 opening reserve loads what's available for that event's window, and shows the sums", async () => {
  const { state, sent } = actions();
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  assert.deepEqual(sent.map(([method, path]) => `${method} ${path}`), ["GET /equipment/availability?event_id=7", "GET /events/7/equipment-reservations"]);
  assert.equal(state.arrangement.reserveOpen, true);
  assert.deepEqual(state.arrangement.availability.map(line => line.available), [5, 0]);
  assert.equal(state.availabilityLabel(available[0]), "Microphone (Audio): 5 available of 10, 3 reserved, 2 unavailable");
  assert.match(row, /x-text="availabilityLabel\(line\)"/);
  // A narrow row cuts the option's text off, so the chosen item's numbers are also shown in full.
  state.arrangement.itemId = "1";
  assert.equal(state.chosenAvailability(), "Microphone (Audio): 5 available of 10, 3 reserved, 2 unavailable");
  state.arrangement.itemId = "";
  assert.equal(state.chosenAvailability(), "");
  assert.match(row, /x-text="chosenAvailability\(\)"/);
  assert.match(row, /:disabled="line\.available < 1"/);
});

test("TC-4322 an event with no window shows the backend's sentence, not an empty form", async () => {
  const { state } = actions({ "GET /equipment/availability?event_id=7": { ok: false, status: 400, data: { detail: "This event has no date or venue booking yet, so its equipment can't be checked or reserved." } } });
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  assert.match(state.arrangement.reserveError, /no date or venue booking/);
  assert.deepEqual(state.arrangement.availability, []);
  assert.match(row, /x-show="arrangement\.availabilityLoaded && arrangement\.availability\.length"/);
});

test("TC-4323 an item with more units held than exist is called out in the reserve panel", async () => {
  const over = { item_id: 3, equipment_type: "Audio", name: "Speaker", total_quantity: 5, reserved: 8, unavailable: 1, available: 0, over_committed: true };
  const { state } = actions({ "GET /equipment/availability?event_id=7": { ok: true, status: 200, data: { items: [{ ...available[0], over_committed: false }, over] } } });
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  assert.deepEqual(state.overCommittedLines().map(line => line.name), ["Speaker"]);
  assert.equal(state.overCommittedLabel(over), "Speaker: 9 units are reserved or unavailable for this event's date and time, but there are only 5. Release some or find more.");
  assert.match(row, /x-for="line in overCommittedLines\(\)"/);
  assert.match(row, /x-text="overCommittedLabel\(line\)"/);
});

test("TC-4421 the reserve form offers the matching item and the quantity requested", async () => {
  const { state } = actions();
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  assert.equal(state.arrangement.itemId, "1");
  assert.equal(state.arrangement.quantity, "2");
  const other = actions();
  other.state.startArrangement(item);
  await other.state.toggleReserve({ ...item, equipment_type: "Stage lights" });
  assert.equal(other.state.arrangement.itemId, "", "nothing matches, so the user chooses");
});

test("TC-4422 reserving sends the item and quantity, says so, and reloads the numbers", async () => {
  const { state, sent } = actions();
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  sent.length = 0;
  await state.reserveForRequest(item);
  assert.deepEqual(sent[0], ["POST", "/events/7/equipment-reservations", { item_id: 1, quantity: 2 }]);
  assert.deepEqual(sent.slice(1).map(([method, path]) => `${method} ${path}`), ["GET /equipment/availability?event_id=7", "GET /events/7/equipment-reservations"]);
  assert.match(state.arrangement.message, /Reserved 2 of Microphone/);
  assert.equal(state.arrangement.reserveBusy, false);
});

test("TC-4423 a form that asks for too many, or for nothing, sends nothing", async () => {
  const { state, sent } = actions();
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  sent.length = 0;
  for (const [itemId, quantity, expected] of [["", "1", /Choose/], ["1", "0", /whole number/], ["1", "1.5", /whole number/], ["1", "6", /Only 5/]]) {
    Object.assign(state.arrangement, { itemId, quantity });
    await state.reserveForRequest(item);
    assert.match(state.arrangement.reserveError, expected);
  }
  assert.deepEqual(sent, []);
});

test("TC-4424 another reservation taking the units first shows the backend's sentence and fresh numbers", async () => {
  const { state, sent } = actions({ "POST /events/7/equipment-reservations": { ok: false, status: 409, data: { detail: "Only 1 of 'Microphone' available for this event's date and time, so 2 can't be reserved." } } });
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  sent.length = 0;
  await state.reserveForRequest(item);
  assert.match(state.arrangement.reserveError, /Only 1 of 'Microphone'/);
  assert.ok(sent.some(([, path]) => path.startsWith("/equipment/availability")), "the numbers are reloaded");
  assert.equal(state.arrangement.message, "");
});

test("TC-4425 a reservation can be released from the row, and a failed release says why", async () => {
  const { state, sent } = actions();
  const item = request();
  state.startArrangement(item);
  await state.toggleReserve(item);
  sent.length = 0;
  await state.releaseReservation(item, held);
  assert.deepEqual(sent[0], ["DELETE", "/events/7/equipment-reservations/9", undefined]);
  assert.match(state.arrangement.message, /Released 2 of Microphone/);
  assert.match(row, /@click="releaseReservation\(item, held\)"/);
  assert.equal(state.heldLabel(held), "2 of Microphone");
  assert.equal(state.heldLabel({ ...held, released_at: "2099-01-02T00:00:00" }), "2 of Microphone (released)");
  assert.match(row, /x-show="!held\.released_at"/);

  const failing = actions({ "DELETE /events/7/equipment-reservations/9": { ok: false, status: 409, data: { detail: "This reservation has already been released." } } });
  failing.state.startArrangement(item);
  await failing.state.toggleReserve(item);
  await failing.state.releaseReservation(item, held);
  assert.match(failing.state.arrangement.reserveError, /already been released/);
});

test("TC-4430 a refused release reloads the list, so a stale row and its button don't stay", async () => {
  const fresh = { ...held, released_at: "2099-01-02T00:00:00", released_by: "tech" };
  const { state, sent } = actions({
    "DELETE /events/7/equipment-reservations/9": { ok: false, status: 409, data: { detail: "This reservation has already been released." } },
    "GET /events/7/equipment-reservations": { ok: true, status: 200, data: { reservations: [fresh] } },
  });
  const item = request();
  state.startArrangement(item);
  state.arrangement.reserveOpen = true;
  state.arrangement.reservations = [held];
  await state.releaseReservation(item, held);
  assert.deepEqual(sent.slice(1).map(([method, path]) => `${method} ${path}`), ["GET /equipment/availability?event_id=7", "GET /events/7/equipment-reservations"]);
  assert.deepEqual(state.arrangement.reservations, [fresh]);
  assert.match(state.arrangement.reserveError, /already been released/, "the reason stays after the reload");
  assert.equal(state.arrangement.message, "");
});

test("TC-4431 releasing hands focus to the row's Reserve button, because the Release button goes away", async () => {
  const { state } = actions();
  const item = request();
  let focused = 0;
  state.$nextTick = callback => callback();
  state.$refs = { reserveToggle: { focus: () => { focused += 1; } } };
  state.startArrangement(item);
  await state.releaseReservation(item, held);
  assert.equal(focused, 1);

  const failing = actions({ "DELETE /events/7/equipment-reservations/9": { ok: false, status: 409, data: { detail: "This reservation has already been released." } } });
  let refusedFocus = 0;
  failing.state.$nextTick = callback => callback();
  failing.state.$refs = { reserveToggle: { focus: () => { refusedFocus += 1; } } };
  failing.state.startArrangement(item);
  await failing.state.releaseReservation(item, held);
  assert.equal(refusedFocus, 1, "a refused release can also remove the button, once the list reloads");
  assert.match(row, /x-ref="reserveToggle"[^>]*@click="toggleReserve\(item\)"/);
});

function detail(reply) {
  const state = eventReservations();
  const sent = [];
  state.dialogErrorMessage = response => response.data.detail;
  state.formatUtc = value => `utc ${value}`;
  state.eventDetail = { id: 7, detail: { assigned_coordinator_id: 2 } };
  state.apiRequest = async path => { sent.push(path); return reply; };
  return { state, sent };
}

test("TC-4426 the event's reservations load in the event details and read in plain words", async () => {
  const released = { ...held, id: 10, released_at: "2099-01-02T00:00:00", released_by: "tech" };
  const { state, sent } = detail({ ok: true, status: 200, data: { reservations: [held, released] } });
  await state.init();
  assert.deepEqual(sent, ["/events/7/equipment-reservations"]);
  assert.equal(state.reservationPanel.loaded, true);
  assert.deepEqual(state.reservationPanel.rows.map(line => line.id), [9, 10]);
  assert.equal(state.reservationState(held), "Reserved");
  assert.equal(state.reservationState(released), "Released utc 2099-01-02T00:00:00");
});

test("TC-4427 a failed load says so, and a refusal doesn't look like an empty list", async () => {
  const { state } = detail({ ok: false, status: 403, data: { detail: "Coordinators can view this only for events assigned to them" } });
  await state.init();
  assert.equal(state.reservationPanel.loaded, false);
  assert.match(state.reservationPanel.error, /assigned to them/);
  assert.match(panel, /x-show="reservationPanel\.loaded && !reservationPanel\.rows\.length"/);
  assert.match(panel, /x-show="reservationPanel\.error"/);
});

test("TC-4428 the panel is at its place, read-only, and only for the assigned Coordinator and the Lead", () => {
  const place = html.indexOf("<!-- [Sprint 5] SCRUM-44 equipment reservations panel: insert directly below this line. -->");
  assert.ok(place > 0 && place < html.indexOf('x-data="eventReservations()"'));
  assert.match(html, /<template x-if="eventDetail\.detail && canRecordEquipment\(eventDetail\.detail\)"><section class="dialog-section equipment-panel" x-data="eventReservations\(\)"/);
  assert.doesNotMatch(panel, /<button|<form|<input|<select/, "Technical Support make the changes; the Coordinator only reads");
  assert.match(main, /Alpine\.data\("eventReservations", eventReservations\);/);
});

test("TC-4429 the panel also shows each request's latest status and note to the Coordinator", () => {
  assert.match(panel, /x-for="line in eventDetail\.equipment\?\.items \|\| \[\]"/);
  assert.match(panel, /x-text="line\.latest_status"/);
  assert.match(panel, /x-text="line\.latest_note/);
});
