/**
 * SCRUM-99: the follow-ups from PR #48's review.
 *
 * Each block below is one follow-up. The harness loads app.js as the other
 * test files do, and can also fix the clock and the browser's address.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { NAVIGATION, normalizeRole, resolveRolePath } from "../src/rbac.js";
import { resetSignedOutState } from "../src/session-state.js";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

// Loads app.js with a fixed clock (when `now` is given), address and document.
function app({ now, hostname = "localhost", document: page = {} } = {}) {
  let factory;
  const context = {
    NAVIGATION,
    normalizeRoleName: normalizeRole,
    resolveRolePath,
    resetSignedOutState,
    requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn(), ...page },
    Alpine: { data: (_, fn) => (factory = fn) },
    window: { location: { hostname }, setInterval: () => 0 },
    fetch: async () => { throw new TypeError("Failed to fetch"); },
  };
  if (now) {
    context.Date = class extends Date {
      constructor(...args) { if (args.length) super(...args); else super(now); }
      static now() { return Date.parse(now); }
    };
  }
  vm.runInNewContext(
    source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""),
    context,
  );
  const state = factory();
  state.user = { user_id: 9, username: "coord1", role: "Coordinator" };
  state.apiRequest = async () => ({ ok: true, status: 200, data: { days: [] } });
  return state;
}

// The browser's own zone must not matter, so run these as a user far from Singapore.
async function inNewYork(check) {
  const original = process.env.TZ;
  process.env.TZ = "America/New_York";
  try {
    await check();
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
}

// ---------------------------------------------------------- calendar on phones

const phoneRules = () => {
  const blocks = [...styles.matchAll(/@media\(max-width:640px\)\{([^\n]*)\}/g)].map(match => match[1]);
  return blocks.join("\n");
};

test("TC-9908 on a phone a calendar day stacks in one column, and the rule beats the two-column .booking-list li", () => {
  // .booking-list li is (0,1,1) with two columns, so the override needs the longer selector.
  assert.match(phoneRules(), /\.booking-list li\.calendar-day\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
});

test("TC-9909 busy blocks lose the list's bullets and its two-column grid, and long text wraps inside the row", () => {
  assert.match(styles, /\.booking-list \.calendar-blocks\{[^}]*list-style:none/);
  assert.match(styles, /\.booking-list \.calendar-blocks\{[^}]*padding:0/);
  assert.match(styles, /\.booking-list \.calendar-blocks li\{[^}]*display:block/);
  assert.match(styles, /\.booking-list \.calendar-blocks li\{[^}]*overflow-wrap:anywhere/);
});

// ---------------------------------------------------------- Singapore days

test("TC-9913 the default range starts on today's Singapore date, whatever the browser's zone", async () => {
  await inNewYork(async () => {
    // 20:00 UTC on 1 March is already 04:00 on 2 March in Singapore, and 15:00 on 1 March in New York.
    const state = app({ now: "2099-03-01T20:00:00Z" });
    await state.openVenueCalendar({ id: 3, name: "Grand Hall" });
    assert.equal(state.venueCalendar.from, "2099-03-02");
    assert.equal(state.venueCalendar.to, "2099-03-08", "seven days, today included");
  });
});

test("TC-9914 a calendar day's label is day-first and names the date asked for, whatever the browser's zone", async () => {
  await inNewYork(() => {
    const label = app().calendarDayLabel("2099-03-09");
    assert.match(label, /^[A-Z][a-z]{2}, 9 Mar$/);
  });
  assert.equal(app().calendarDayLabel("not a date"), "not a date", "an unreadable date is shown as sent");
  assert.equal(app().calendarDayLabel(""), "Not set");
});

// ---------------------------------------------------------- focus after closing

const fakeButton = () => {
  const button = { isConnected: true, focused: 0, focus() { this.focused += 1; } };
  return button;
};

test("TC-9915 closing the calendar returns focus to the button that opened it, and that button stays out of reactive state", async () => {
  const opener = fakeButton();
  const page = { activeElement: opener };
  const state = app({ document: page });
  state.$nextTick = callback => callback();
  // Alpine wraps state in a proxy, so a DOM element kept on it would be wrapped and watched.
  await state.openVenueCalendar({ id: 3, name: "Grand Hall" });
  assert.equal(Object.values(state).includes(opener), false);
  assert.equal(Object.values(state.venueCalendar).includes(opener), false);
  assert.equal(opener.focused, 0, "focus moves only on closing");
  state.closeVenueCalendar();
  assert.equal(state.venueCalendar, null);
  assert.equal(opener.focused, 1);
});

test("TC-9916 closing the calendar leaves focus alone when the opening button has gone from the page", async () => {
  const opener = fakeButton();
  const state = app({ document: { activeElement: opener } });
  state.$nextTick = callback => callback();
  await state.openVenueCalendar({ id: 3, name: "Grand Hall" });
  opener.isConnected = false; // for example, the search results changed behind the calendar
  state.closeVenueCalendar();
  assert.equal(state.venueCalendar, null);
  assert.equal(opener.focused, 0);
});

test("TC-9917 a role that can't open the calendar doesn't take focus when closing", async () => {
  const opener = fakeButton();
  const state = app({ document: { activeElement: opener } });
  state.$nextTick = callback => callback();
  state.user = { user_id: 1, username: "org1", role: "Organiser" };
  await state.openVenueCalendar({ id: 3, name: "Grand Hall" });
  assert.equal(state.venueCalendar, null);
  state.closeVenueCalendar();
  assert.equal(opener.focused, 0);
});

// ---------------------------------------------------------- equipment quantity

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

test("TC-9920 the equipment form accepts the largest quantity and refuses one above it before calling the server", () => {
  const state = app();
  const check = quantity => state.validateEquipment({ equipment_type: "Projector", quantity }, []).quantity;
  assert.equal(check("2147483647"), undefined);
  assert.match(check("2147483648"), /2,147,483,647 or fewer/);
  assert.match(check("99999999999999999999999999"), /2,147,483,647 or fewer/);
  assert.match(html, /id="equipment-quantity" type="number" min="1" max="2147483647"/);
});
