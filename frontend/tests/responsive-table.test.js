import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const components = readFileSync(new URL("../src/components.css", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const cards = name => {
  const start = components.indexOf(`@container ${name} (`);
  assert.ok(start > 0, name);
  return components.slice(start, components.indexOf("\n}\n", start));
};

test("TC-10021 the attention bar belongs to the row: the first cell's edge in the table, the whole card when stacked", () => {
  assert.match(components, /\.data-table tr\.needs-attention > td:first-child \{ box-shadow: inset 3px 0 var\(--warning-accent\); \}/);
  for (const name of ["data-table", "data-table-wide"]) {
    const block = cards(name);
    assert.match(block, /\.data-table:not\(\.data-table-compact\) tr\.needs-attention \{ box-shadow: inset 3px 0 var\(--warning-accent\); \}/, name);
    assert.match(block, /\.data-table:not\(\.data-table-compact\) tr\.needs-attention > td:first-child \{ box-shadow: none; \}/, name);
    // Cards are padded 16px, so content is 13px clear of the 3px bar, all on one left edge
    assert.match(block, /tr \{\s*display: flex; flex-wrap: wrap; gap: var\(--space-4\) var\(--space-5\);\s*border-top: 1px solid var\(--border\); padding: var\(--space-4\);/, name);
    assert.match(block, /td \{ flex: 1 1 100%; min-width: 0; border: 0; padding: 0; text-align: left; \}/, name);
    assert.match(block, /td\.cell-inline \{ flex: 1 1 9rem; \}/, name);
    assert.match(block, /content: attr\(data-label\); display: block; margin-bottom: var\(--space-1\);/, name);
  }
  assert.doesNotMatch(styles, /needs-attention|waiting-row/, "no page keeps its own copy of the bar");
});

test("TC-10022 the Lead table stacks at 64rem; Proposed and Status pair up; the coordinator controls wrap", () => {
  assert.match(components, /\.table-container-wide \{ container: data-table-wide \/ inline-size; \}/);
  assert.match(html, /<section class="queue-panel lead-table table-container-wide"/);
  assert.match(html, /<td data-label="Proposed" class="cell-nowrap cell-inline"/);
  assert.match(html, /<td data-label="Status" class="cell-inline"><span class="status-badge" :class="statusClass\(event.status\)">/);
  assert.match(styles, /\.lead-cell-coordinator\{width:18\.5rem;min-width:18\.5rem\}/);
  assert.match(styles, /\.lead-assign-form \.picker\{flex:1 1 7rem;width:7rem;min-width:7rem\}/);
  assert.match(styles, /@container data-table-wide \(max-width: 64rem\)\{\.lead-cell-coordinator\{width:auto;min-width:0\}\n[^\n]*\n\.lead-assign-form\{flex-wrap:wrap;row-gap:var\(--space-2\)\}\n\.lead-assign-form \.picker\{flex:1 1 12rem\}/);
  assert.match(components, /\.picker-trigger span \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
  // The Venue Staff booking table pairs its date and status the same way
  assert.match(html, /<td data-label="Date and time" class="booking-cell-when cell-inline">/);
});
