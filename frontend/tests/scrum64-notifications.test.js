import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { resetSignedOutState } from "../src/session-state.js";

const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
function app() {
  let factory;
  let poll;
  const context = {
    NAVIGATION: [],
    requestedFrontendPath: () => null,
    document: { addEventListener: (_, fn) => fn() },
    Alpine: { data: (_, fn) => { factory = fn; } },
    window: { location: { hash: "" }, setInterval: (fn, delay) => { poll = { fn, delay }; } },
  };
  vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ""), context);
  return { state: factory(), getPoll: () => poll };
}

test("fetch retains every complete message and polling refreshes notifications", async () => {
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
  assert.match(html, /<details class="notification-summary"/);
  assert.match(html, /x-for="notification in notifications"/);
  assert.match(html, /x-text="notification.message"/);
  assert.match(html, /notifications.length/);
});

test("late responses cannot restore notifications after logout or account switch", async () => {
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
