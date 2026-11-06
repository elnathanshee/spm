/**
 * SCRUM-99: the follow-ups from PR #48's review.
 *
 * Each block below is one follow-up. The harness loads app.js as the other
 * test files do, and can also fix the clock and the browser's address.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

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
