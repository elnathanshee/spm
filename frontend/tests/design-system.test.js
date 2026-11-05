import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const read = path => readFileSync(new URL(path, import.meta.url), "utf8");
const source = read("../app.js");
const html = read("../index.html");
const tokens = read("../src/tokens.css");
const components = read("../src/components.css");
const styles = read("../src/styles.css");

function app(role = "coordinator") {
  let factory;
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } }, window: { location: {} },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.user = { user_id: 1, username: "coord1", role: normalizeRole(role) };
  return state;
}

function withTimeZone(zone, fn) {
  const previous = process.env.TZ;
  process.env.TZ = zone;
  try { return fn(); } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
}

// A token's hex value, following aliases such as --surface: var(--neutral-0).
const tokenValue = name => {
  const value = tokens.match(new RegExp(`${name}:\\s*([^;]+);`))?.[1].trim();
  const alias = value?.match(/^var\((--[\w-]+)\)$/);
  return alias ? tokenValue(alias[1]) : value;
};
function contrast(a, b) {
  const lum = hex => {
    const [r, g, b2] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b2;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test("TC-10121 colors, font sizes and radii outside tokens.css come from tokens", () => {
  for (const [name, css] of [["components.css", components], ["styles.css", styles]]) {
    assert.deepEqual(css.match(/#[0-9a-fA-F]{3,6}\b/g) || [], [], `${name} has raw hex colors`);
    assert.deepEqual(css.match(/font-size:\s*[0-9.]+(rem|px|em)/g) || [], [], `${name} has raw font sizes`);
  }
  assert.match(styles, /@import "\.\/tokens\.css";\s*@import "\.\/components\.css";/);
});

test("TC-10122 the serif is used only for intentional display text", () => {
  assert.doesNotMatch(components + styles, /Georgia|serif\b(?!.*--font)/);
  const displayRules = (components + styles).replace(/\/\*[\s\S]*?\*\//g, "").split("}").filter(rule => rule.includes("var(--font-display)")).map(rule => rule.split("{")[0].trim());
  assert.deepEqual(displayRules.sort(), [".display-title", ".login-card h2", ".login-story h1"]);
});

test("TC-10123 every backend status has one badge tone, and that tone exists", () => {
  const state = app();
  const eventStatuses = [...read("../../backend/status.py").matchAll(/=\s*"([^"]+)"/g)].map(match => match[1]);
  const bookingStatuses = ["Pending", "Confirmed", "Rejected", "Expired", "Approved", "Tentative"];
  for (const status of [...eventStatuses, ...bookingStatuses]) {
    const tone = state.statusClass(status);
    const statusTones = source.slice(source.indexOf("const STATUS_TONES"), source.indexOf("};", source.indexOf("const STATUS_TONES")));
    assert.ok(statusTones.includes(`${status}: "`) || statusTones.includes(`"${status}": "`), `${status} has its own entry in STATUS_TONES`);
    assert.match(components, new RegExp(`\\.${tone} \\{`), `${status} -> ${tone} has a style`);
    assert.equal(state.bookingStatusClass(status), tone, `${status} looks the same for events and bookings`);
  }
  assert.equal(state.statusClass("Draft"), "badge-neutral");
  assert.notEqual(state.statusClass("Rejected"), state.statusClass("Expired"));
  // Badges always show the status text itself
  assert.doesNotMatch(html, /Awaiting review'/);
  // A status badge shows the same status it is coloured by
  const statusBadges = [...html.matchAll(/:class="(?:booking)?[sS]tatusClass\(([^)]+)\)"[^>]*>(?:<i><\/i>)?<span x-text="([^"]+)"|:class="(?:booking)?[sS]tatusClass\(([^)]+)\)" x-text="([^"]+)"/g)];
  // Lists sectioned by status (Organiser, Coordinator queue) carry no row badge (UI_DECISIONS D4)
  assert.ok(statusBadges.length >= 6);
  for (const match of statusBadges) assert.equal(match[2] ?? match[4], match[1] ?? match[3], match[0]);
});

test("TC-10124 one date and time format, in Singapore time, whatever the machine's time zone", () => {
  const state = app();
  for (const zone of ["UTC", "America/New_York", "Asia/Singapore"]) withTimeZone(zone, () => {
    assert.equal(state.formatHoldExpiry("2026-11-04T03:55:00"), "4 Nov 2026, 11:55 am", zone);
    assert.equal(state.formatNotificationTime("2026-11-04T03:55:11.861194"), "4 Nov 2026, 11:55 am", zone);
    assert.equal(state.formatDate("2027-10-15T18:00:00"), "15 Oct 2027, 6:00 pm", zone);
    assert.equal(state.formatSchedule("2026-11-07T08:45:00", "2026-11-07T13:45:00"), "7 Nov 2026, 4:45 pm – 9:45 pm", zone);
    assert.equal(state.formatSchedule("2026-11-07T15:00:00", "2026-11-07T17:00:00"), "7 Nov 2026, 11:00 pm – 8 Nov 2026, 1:00 am", zone);
    assert.equal(state.inputToDate("2026-11-20T10:00").toISOString(), "2026-11-20T02:00:00.000Z", zone);
    assert.equal(state.formatServerTimes("expires at 2099-04-09 14:18 UTC"), "expires at 9 Apr 2099, 10:18 pm", zone);
  });
  assert.equal(state.formatDate(null), "Not set");
  assert.equal(state.formatHoldExpiry("nonsense"), "Not set");
  assert.equal(state.inputToDate("not a date"), null);
  assert.match(html, /class="timezone-note"[^>]*>Times in SGT</);
});

test("TC-10125 one initials rule for avatars and monograms", () => {
  const state = app();
  assert.equal(state.initials("organiser1"), "OR");
  assert.equal(state.initials("Harbour Ballroom"), "HB");
  assert.equal(state.initials("venue_staff.lead"), "VS");
  assert.equal(state.initials(""), "?");
  assert.equal(state.userInitials(), "CO");
  assert.doesNotMatch(html, /charAt\(0\)/);
  // Venue catalogue and booking review; booking rows are a data table now, without a monogram.
  assert.equal((html.match(/x-text="initials\(/g) || []).length, 2);
});

test("TC-10126 every form control has a label, and every icon-only button a name", () => {
  const ids = [...html.matchAll(/<(?:input|select|textarea)\b[^>]*?\s(:?id)="([^"]+)"/g)].map(match => [match[1], match[2]]);
  assert.ok(ids.length > 20);
  for (const [binding, id] of ids) {
    const attr = binding === ":id" ? ":for" : "for";
    assert.ok(html.includes(`${attr}="${id}"`), `no <label ${attr}="${id}">`);
  }
  const unnamed = (html.match(/<(?:input|select|textarea)\b(?![^>]*\s:?id=)[^>]*>/g) || []).filter(tag => !/aria-label|type="checkbox"/.test(tag));
  assert.deepEqual(unnamed, []);
  for (const button of html.match(/<button[^>]*>×<\/button>/g) || []) assert.match(button, /aria-label="[^"]+"/, button);
});

test("TC-10127 token text colors meet WCAG AA on the surfaces they are used on", () => {
  const pairs = [
    ["--text-muted", "--surface-canvas"], ["--text-muted", "--surface"], ["--text-secondary", "--surface"],
    ["--brand-600", "--surface"], ["--danger", "--surface"], ["--text-placeholder", "--surface"],
    ["--text-inverse-muted", "--brand-900"], ["--text-inverse-subtle", "--brand-900"],
  ];
  for (const tone of ["neutral", "info", "waiting", "attention", "success", "danger", "muted"]) pairs.push([`--tone-${tone}-fg`, `--tone-${tone}-bg`]);
  for (const [fg, bg] of pairs) {
    const ratio = contrast(tokenValue(fg), tokenValue(bg));
    assert.ok(ratio >= 4.5, `${fg} on ${bg} is ${ratio.toFixed(2)}:1`);
  }
  assert.ok(contrast(tokenValue("--neutral-500"), "#ffffff") >= 3, "control borders reach 3:1");
  assert.match(components, /:focus-visible \{ outline: none; box-shadow: var\(--focus-ring\); \}/);
});

test("TC-10128 success messages are toasts that clear themselves; errors stay inline", () => {
  assert.match(html, /<div class="toast-region" role="status" aria-live="polite"><template x-if="alert && alert.message && alert.kind === 'success'">/);
  assert.match(html, /class="alert content-alert"[^>]*x-show|x-show="alert && alert.message && alert.kind !== 'success'"[^>]*class="alert content-alert"/);
  const state = app();
  state.showAlert("Saved.", "success");
  assert.equal(state.alert.kind, "success");
});
