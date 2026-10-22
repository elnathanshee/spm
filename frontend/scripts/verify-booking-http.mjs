// Invoked by pytest against disposable SQLite; uses real Vite proxy and HTTP APIs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import net from "node:net";
import { createServer, loadConfigFromFile } from "vite";
import { reactive } from "@vue/reactivity";
import { normalizeRole } from "../src/rbac.js";

async function freePort() {
  const socket = net.createServer();
  await new Promise((resolve, reject) => { socket.once("error", reject); socket.listen(0, "127.0.0.1", resolve); });
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

const [backend, eventId] = process.argv.slice(2);
assert.ok(backend?.startsWith("http://127.0.0.1:"));
const { config } = await loadConfigFromFile({ command: "serve", mode: "test" });
// Reproduce the old missing-proxy behavior without changing repository config.
const legacyProxy = { ...config.server.proxy };
delete legacyProxy["/venues"];
const legacy = await createServer({ ...config, configFile: false,
  server: { ...config.server, proxy: legacyProxy, port: await freePort(), open: false } });
await legacy.listen();
try {
  const response = await fetch(`http://127.0.0.1:${legacy.httpServer.address().port}/venues`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /text\/html/);
  console.log("Previous missing /venues proxy reproduced: HTTP 200 HTML instead of a venue array.");
} finally {
  await legacy.close();
}
assert.ok(config.server.proxy["/venues"], "Vite must proxy venue requests");
for (const proxy of Object.values(config.server.proxy)) proxy.target = backend;
const server = await createServer({ ...config, configFile: false,
  server: { ...config.server, port: await freePort(), open: false } });
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let token = "staff-http";
const requests = [];
const request = async (path, options = {}) => {
  const response = await fetch(`${origin}${path}`, {
    ...options, headers: { ...options.headers, Cookie: `connectsphere_session=${token}` },
  });
  requests.push([options.method || "GET", path, response.status]);
  return response;
};
const json = async (path, body) => {
  const response = await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  assert.ok(response.ok, `${path}: ${response.status}`);
  return response.json();
};
function createState() {
    const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
    let factory;
    const context = { NAVIGATION: [], normalizeRoleName: normalizeRole,
      requestedFrontendPath: () => null, document: { addEventListener: (_, fn) => fn() },
      Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} }, fetch: request };
    vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
    return reactive(factory());
}

try {
  // Empty results traverse the actual frontend proxy, not a mocked apiRequest.
  let response = await request("/venues");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /application\/json/);
  assert.deepEqual(await response.json(), []);
  const staff = createState();
  staff.user = { user_id: 3, role: "venue_staff" };
  await staff.fetchVenueWorkspace();
  assert.equal(staff.venueWorkspace.venues.length, 0);
  Object.assign(staff.venueWorkspace.form, { name: "HTTP test hall", location: "Test", capacity: "100" });
  await staff.createVenue();
  assert.equal(staff.alert.kind, "success");
  const venue = staff.venueWorkspace.venues[0];
  for (const [index, role] of ["coordinator", "lead"].entries()) {
    token = `${role}-http`;
    const state = createState();
    const event = { id: Number(eventId), title: "HTTP booking" };
    state.user = { user_id: index + 1, role };
    state.assignedEvents = [event]; state.leadEvents = [event];
    await state.openBookings(event);
    assert.equal(state.bookingDialog.ready, true);
    assert.equal(state.bookingDialog.busy, false);
    assert.equal(state.bookingDialog.venues[0].id, venue.id);
    assert.equal(state.bookingDialog.bookingsLoaded, true);
    Object.assign(state.bookingDialog.form, { venue_id: String(venue.id),
      booking_start: `2099-04-${10 + index}T10:00`, booking_end: `2099-04-${10 + index}T12:00` });
    await state.submitBooking();
    assert.equal(state.alert.kind, "success");
    assert.equal(state.bookingDialog.submitting, false);
    const booking = state.bookingDialog.bookings.find(b => b.booking_start.startsWith(`2099-04-${10 + index}`));
    assert.equal(booking.status, "Pending");
    token = "staff-http";
    await staff.fetchNotifications();
    const notification = staff.notifications.find(n => n.booking_id === booking.id);
    assert.equal(notification.kind, "booking_submitted");
    assert.equal(notification.read, false);
    token = "staff2-http";
    const otherNotifications = await (await request("/notifications")).json();
    assert.ok(otherNotifications.some(n => n.booking_id === booking.id && !n.read));
    token = "staff-http";
    await staff.markNotificationRead(notification);
    assert.equal(staff.notifications.find(n => n.id === notification.id).read, true);
    await staff.openStaffBooking(notification.booking_id);
    assert.equal(staff.staffBooking.detail.requester_id, index + 1);
    const action = index ? "reject" : "approve";
    staff.staffBooking.reason = "HTTP persisted rejection";
    await staff.decideStaffBooking(action);
    assert.equal(staff.staffBooking.detail.status, index ? "Rejected" : "Confirmed");
    token = `${role}-http`;
    await state.openBookings(event);
    const decided = state.bookingDialog.bookings.find(b => b.id === booking.id);
    assert.equal(decided.status, index ? "Rejected" : "Confirmed");
    if (index) assert.equal(decided.rejection_reason, "HTTP persisted rejection");
    await state.fetchNotifications();
    assert.ok(state.notifications.some(n => n.kind === (index ? "booking_rejected" : "booking_approved")));
  }
  assert.ok(requests.every(([, , status]) => status < 400), JSON.stringify(requests));
  console.log("Real Vite -> FastAPI HTTP workflow passed for Coordinator and Lead; submission, decisions and notifications verified.");
} finally {
  await server.close();
}
