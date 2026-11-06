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
  // Lead table: the assignment controls sit in the Coordinator column, Venue bookings alone in Actions (right-aligned)
  const lead = html.slice(html.indexOf('x-for="event in filteredLeadEvents()"'), html.indexOf("</tr></template>", html.indexOf('x-for="event in filteredLeadEvents()"')));
  assert.match(lead, /<td data-label="Coordinator" class="lead-cell-coordinator"><div class="assignment-controls" x-show="canAssignEvent\(event\)">/);
  assert.match(lead, /<td data-label="Actions" class="cell-end"><button type="button" class="button button-secondary button-compact" x-show="canBookEvent\(event\)" @click="openBookings\(event\)">/);
  assert.doesNotMatch(lead, />Open request</, "the title opens the request");
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
  assert.deepEqual(statuses, ["Returned", "Draft", "Submitted", "Under Review", "Approved", "Planning", "Safety Review", "Confirmed", "Rejected", "Completed", "Cancelled"]);
  assert.doesNotMatch(organiserRow, /status-badge/);
  assert.match(html, /eventDetail\.detail"><div><span class="status-badge"/);
});

test("UI-D5 list rows keep actions on the right, open through the title (Coordinator) or View details first (Organiser), at most one filled button", () => {
  // Shared by the Organiser and Coordinator lists since the Coordinator redesign (UI_DECISIONS item 9)
  assert.match(components, /\.request-row\{display:flex;flex-wrap:wrap;align-items:center;/);
  assert.match(components, /\.request-summary\{flex:1 1 18rem;min-width:0\}/);
  assert.match(components, /\.request-actions\{justify-content:flex-end;flex-wrap:wrap;margin-left:auto\}/, "actions keep to the right edge, even when they wrap below");
  assert.doesNotMatch(styles, /\.request-row\{/, "the request list lives in components.css only");
  assert.match(organiserRow, /<div class="request-actions button-group">\s*<button type="button" class="button button-secondary" @click="openEventDetail\(event\.id\)">View details/);
  assert.equal((organiserRow.match(/button-primary/g) || []).length, 1);
  const start = html.indexOf('x-for="event in group.events"', html.indexOf("currentPath === '/coordinator/events'"));
  const coordinatorRow = html.slice(start, html.indexOf("</li></template>", start));
  assert.match(coordinatorRow, /<h3><a class="row-link" :href="`#request-\$\{event\.id\}`" @click\.prevent="openEventDetail\(event\.id\)" x-text="event\.title"><\/a><\/h3>/);
  assert.doesNotMatch(coordinatorRow, /View details/, "no separate open button");
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

test("UI-D8 no summary metric repeats a filter or section count on the Organiser and Coordinator pages", () => {
  for (const path of ["/events", "/coordinator/events", "/coordinator/all-events"]) {
    const start = html.indexOf(`currentPath === '${path}' && user.role`);
    // The page runs to the next page template, or the end of <main>
    const next = [html.indexOf('<template x-if="currentPath', start + 1), html.indexOf("</main>", start)].filter(at => at > 0);
    const page = html.slice(start, Math.min(...next));
    assert.ok(start > 0, path);
    assert.doesNotMatch(page, /summary-strip|attention-pill/, `${path} has a summary metric`);
  }
});

test("UI-D6b on phones the row is a column, so nothing in it keeps a width-based flex basis as its height", () => {
  const phone = components.slice(components.indexOf("/* ---------- Request list"), components.indexOf("/* ---------- Tables"));
  const block = phone.slice(phone.indexOf("@media (max-width: 640px)"));
  assert.match(block, /\.request-row\{flex-direction:column;/);
  assert.match(block, /\.request-summary\{flex:none\}/);
  assert.match(block, /\.request-note\{flex-basis:auto\}/);
});

test("UI-D9 rows open through a link on the title; tables align cells with headers; people lists aren't number cards", () => {
  const links = html.match(/<a class="row-link" :href="`#request-\$\{event\.id\}`" @click\.prevent="openEventDetail\(event\.id\)" x-text="event\.title"><\/a>/g) || [];
  assert.equal(links.length, 3, "Coordinator assigned, Coordinator all events, Lead");
  assert.match(components, /\.row-link:focus-visible \{ box-shadow: var\(--focus-ring\); \}/);
  assert.match(components, /\.row-link:hover \{[^}]*text-decoration: underline;/);
  // The Lead requests and workload are real tables, so cells line up with their headers
  const lead = html.slice(html.indexOf("currentPath === '/lead/events'"), html.indexOf("currentPath === '/coordinator/all-events'"));
  assert.equal((lead.match(/<table class="data-table/g) || []).length, 2);
  assert.match(lead, /<thead><tr><th scope="col">Request<\/th><th scope="col">Proposed<\/th><th scope="col">Status<\/th><th scope="col">Coordinator<\/th><th scope="col" class="cell-end">Actions<\/th><\/tr><\/thead>/);
  assert.doesNotMatch(lead, /summary-strip|operations-summary/, "the workload is a compact table, not number cards");
  assert.match(lead, /<details class="disclosure lead-workload"/, "collapsed by default (no open attribute)");
  assert.doesNotMatch(lead, /<details[^>]* open/);
});
