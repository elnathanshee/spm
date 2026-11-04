import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { normalizeRole, resolveRolePath } from "../src/rbac.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

// A fresh app with its own storage and viewport width.
function app({ width = 1440, saved, storageThrows = false } = {}) {
  let factory;
  const store = new Map(saved === undefined ? [] : [["connectsphere.sidebarCollapsed", saved]]);
  const listeners = [];
  const localStorage = {
    getItem: key => { if (storageThrows) throw new Error("blocked"); return store.has(key) ? store.get(key) : null; },
    setItem: (key, value) => { if (storageThrows) throw new Error("blocked"); store.set(key, value); },
  };
  const window = {
    location: {}, innerWidth: width, setInterval: () => {}, history: { replaceState: () => {} },
    matchMedia: query => ({ matches: query === "(max-width: 767px)" && width < 768, addEventListener: (_, fn) => listeners.push(fn) }),
  };
  const context = {
    NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, requestedFrontendPath: () => null, localStorage, window,
    document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => { factory = fn; } },
  };
  context.globalThis = context;
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  const state = factory();
  state.loadSession = async () => {};
  return { state, store, resize: matches => listeners.forEach(fn => fn({ matches })) };
}

test("TC-9921 the desktop control collapses the sidebar and remembers it between page loads", () => {
  const { state, store } = app();
  assert.equal(state.sidebarCollapsed, false);
  assert.equal(state.sidebarExpanded(), true);
  state.toggleSidebar();
  assert.equal(state.sidebarCollapsed, true);
  assert.equal(store.get("connectsphere.sidebarCollapsed"), "true");
  assert.equal(app({ saved: "true" }).state.sidebarCollapsed, true, "a saved choice wins on the next load");
  assert.equal(app({ width: 900, saved: "false" }).state.sidebarCollapsed, false);
  assert.equal(app({ width: 900 }).state.sidebarCollapsed, true, "with no saved choice, narrow screens start as a rail");
  const blocked = app({ storageThrows: true });
  blocked.state.toggleSidebar();
  assert.equal(blocked.state.sidebarCollapsed, true, "blocked storage still collapses for this visit");
});

test("TC-9922 below 768px the same control opens and closes an overlay, and widening closes it", async () => {
  const { state, store, resize } = app({ width: 375 });
  await state.init();
  assert.equal(state.compactNav, true);
  assert.equal(state.sidebarExpanded(), false);
  state.toggleSidebar();
  assert.equal(state.sidebarOpen, true);
  assert.equal(state.sidebarExpanded(), true);
  assert.equal(store.size, 0, "opening the overlay isn't saved as a desktop preference");
  resize(false);
  assert.equal(state.compactNav, false);
  assert.equal(state.sidebarOpen, false);
});

test("TC-9923 the control is a labelled 40px icon button beside the breadcrumb", () => {
  assert.match(html, /<div class="topbar-start"><button type="button" class="sidebar-toggle icon-control"[^>]*:aria-expanded="sidebarExpanded\(\)" aria-controls="app-sidebar" :aria-label="sidebarExpanded\(\) \? 'Collapse sidebar' : 'Expand sidebar'"[^>]*x-html="iconFor\(compactNav \? 'menu' : 'panel-left'\)"><\/button><div class="breadcrumb">/);
  assert.doesNotMatch(html, /menu-button/);
  assert.match(styles, /\.icon-control\{display:grid;width:2\.5rem;height:2\.5rem;/);
  // Collapsed nav labels are hidden visually, not removed, so buttons keep their names
  assert.match(html, /<span class="nav-label" x-text="item\.label"><\/span>/);
  assert.match(styles, /\.sidebar-collapsed \.nav-label,[^{]*\{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect\(0 0 0 0\)/);
});
