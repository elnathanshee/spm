// Guards for the UI decisions recorded in UI_DECISIONS.md. If one of these fails, a
// change has undone a decision: update UI_DECISIONS.md first if the change is intended.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const read = path => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../index.html");
const app = read("../app.js");
const tokens = read("../src/tokens.css");
const components = read("../src/components.css");
const styles = read("../src/styles.css");
const organiserRow = (() => {
  const start = html.indexOf('x-for="event in group.events"');
  return html.slice(start, html.indexOf("</li></template>", start));
})();

test("UI-D1 a status badge above a details grid gets 16px of space (components.css)", () => {
  assert.match(components, /\.status-badge \+ \.booking-detail-grid \{ margin-top: var\(--space-4\); \}/);
  assert.doesNotMatch(styles, /booking-detail-grid/, "the detail grid lives in components.css only");
  assert.match(html, /<span class="status-badge" :class="statusClass\(eventDetail\.detail\.status\)" x-text="eventDetail\.detail\.status"><\/span><dl class="booking-detail-grid event-detail-fields">/);
});

test("UI-D2 related buttons sit together in a .button-group with the shared 12px gap", () => {
  assert.match(tokens, /--gap-actions: var\(--space-3\);/);
  assert.match(components, /\.button-row, \.button-group,[^{]*\{\s*display: flex; flex-wrap: wrap; align-items: center; gap: var\(--gap-actions\);/);
  assert.match(html, /<div class="lead-row-actions"><div class="button-group"><button[^>]*>Open request<\/button><button[^>]*>.*?Venue bookings<\/button><\/div><div class="assignment-controls"/);
  assert.match(styles, /\.lead-row-actions>\.assignment-controls\{margin-left:auto\}/);
});

test("UI-D3 summary card labels sit 12px after their accent line; numbers stay right (components.css)", () => {
  assert.match(components, /\.operations-summary > div \{\s*position: relative; justify-content: flex-start; gap: var\(--space-3\);/);
  assert.match(components, /\.operations-summary > div > span \{ flex: 1; min-width: 0; overflow-wrap: break-word; \}/);
  assert.match(components, /\.operations-summary > div > strong \{ flex: none; \}/);
  assert.doesNotMatch(styles, /summary-strip|operations-summary/, "summary cards live in components.css only");
});

test("UI-D4 Organiser list: one section per status, no row badges; the details panel keeps its badge", () => {
  const groups = app.slice(app.indexOf("const ORGANISER_GROUPS = ["), app.indexOf("];", app.indexOf("const ORGANISER_GROUPS = [")));
  const statuses = [...groups.matchAll(/statuses: \["([^"]+)"\]/g)].map(match => match[1]);
  assert.deepEqual(statuses, ["Returned", "Draft", "Submitted", "Under Review", "Approved", "Planning", "Confirmed", "Rejected", "Completed", "Cancelled"]);
  assert.doesNotMatch(organiserRow, /status-badge/);
  assert.match(html, /eventDetail\.detail"><div><span class="status-badge"/);
});

test("UI-D5 list rows keep actions on the right, View details first, at most one filled button", () => {
  // Shared by the Organiser and Coordinator lists since the Coordinator redesign (UI_DECISIONS item 9)
  assert.match(components, /\.request-row\{display:flex;flex-wrap:wrap;align-items:center;/);
  assert.match(components, /\.request-summary\{flex:1 1 18rem;min-width:0\}/);
  assert.match(components, /\.request-actions\{justify-content:flex-end;flex-wrap:wrap;margin-left:auto\}/, "actions keep to the right edge, even when they wrap below");
  assert.doesNotMatch(styles, /\.request-row\{/, "the request list lives in components.css only");
  assert.match(organiserRow, /<div class="request-actions button-group">\s*<button type="button" class="button button-secondary" @click="openEventDetail\(event\.id\)">View details/);
  assert.equal((organiserRow.match(/button-primary/g) || []).length, 1);
  const start = html.indexOf('x-for="event in group.events"', html.indexOf("currentPath === '/coordinator/events'"));
  const coordinatorRow = html.slice(start, html.indexOf("</li></template>", start));
  assert.match(coordinatorRow, /<div class="request-actions button-group">\s*<button type="button" class="button button-secondary" @click="openEventDetail\(event\.id\)">View details/);
  assert.equal((coordinatorRow.match(/button-primary/g) || []).length, 1);
  assert.doesNotMatch(coordinatorRow, /status-badge/);
});

test("UI-D6 the sidebar control is a quiet .icon-control sized by tokens", () => {
  assert.match(tokens, /--icon-control-size: 2\.5rem;/);
  assert.match(tokens, /--sidebar-width-expanded: 14\.5rem;\s*--sidebar-width-rail: 4\.5rem;\s*--sidebar-width-overlay: min\(17rem, 85vw\);/);
  assert.match(components, /\.icon-control \{\s*display: grid; width: var\(--icon-control-size\); height: var\(--icon-control-size\);/);
  assert.doesNotMatch(styles, /\.icon-control\{/, "the icon control lives in components.css only");
  assert.match(styles, /@media\(max-width:767px\)\{\.app-shell\{--sidebar-width:var\(--sidebar-width-overlay\)\}/);
  assert.match(app, /const COMPACT_NAV_QUERY = "\(max-width: 767px\)";/, "JS and CSS use the same 768px breakpoint");
  assert.match(html, /class="sidebar-toggle icon-control"/);
  assert.match(html, /class="notification-trigger icon-control"/);
});

test("UI-D7 dates use one formatter in Singapore time; the SGT pill stays until BACKEND_ISSUES #12", () => {
  assert.match(app, /const DISPLAY_TIME_ZONE = "Asia\/Singapore";/);
  // Every visible date goes through a shared formatter
  const raw = [...html.matchAll(/x-text="([^"]*(?:_at|_start|_end)\b[^"]*)"/g)].map(match => match[1])
    .filter(text => !/format(Date|Utc|Schedule|HoldExpiry|NotificationTime|DateTime|Range)\(|eventFieldValue\(|fieldErrors\./.test(text) && !/^event\.submitted_at \? /.test(text));
  assert.deepEqual(raw, []);
  assert.match(html, /class="timezone-note"[^>]*>Times in SGT</);
});
