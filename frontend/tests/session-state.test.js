import assert from "node:assert/strict";
import test from "node:test";

import { beginAuthentication, finishAuthentication, resetSignedOutState } from "../src/session-state.js";

test("logout clears notifications and all authenticated UI state", () => {
  const state = {
    user: { user_id: 2, username: "coord2", role: "Coordinator" },
    currentPath: "/coordinator/events",
    sidebarOpen: true,
    loading: true,
    accessDenied: true,
    unbuiltRole: true,
    events: [{ id: 1 }],
    assignedEvents: [{ id: 2 }],
    allCoordinatorEvents: [{ id: 4 }],
    leadEvents: [{ id: 3 }],
    coordinators: [{ id: 2 }],
    notifications: [{ id: 1 }],
    alert: { message: "Review started.", kind: "success" },
    fieldErrors: { reason: "Required" },
    loginForm: { username: "coord2", password: "secret" },
    draft: { title: "Private event data" },
    amendForm: { title: "Private event data", description: "Details" },
    decisionForm: { reason: "Reviewed" },
    modal: { open: true, kind: "return", event: { id: 2 } },
  };

  resetSignedOutState(state);

  assert.equal(state.user, null);
  assert.equal(state.currentPath, "/login");
  assert.equal(state.alert, null);
  assert.deepEqual(state.events, []);
  assert.deepEqual(state.assignedEvents, []);
  assert.deepEqual(state.allCoordinatorEvents, []);
  assert.deepEqual(state.leadEvents, []);
  assert.deepEqual(state.coordinators, []);
  assert.deepEqual(state.notifications, []);
  assert.deepEqual(state.loginForm, { username: "", password: "" });
  assert.deepEqual(state.fieldErrors, {});
  assert.deepEqual(state.modal, { open: false, kind: "", event: null });
  assert.equal(state.sidebarOpen, false);
  assert.equal(state.loading, false);
  assert.equal(state.accessDenied, false);
  assert.equal(state.unbuiltRole, false);
  assert.equal(state.showPassword, false);
});

test("authentication state prevents duplicate login submissions", () => {
  const state = { loading: false, alert: { message: "Old error", kind: "error" } };

  assert.equal(beginAuthentication(state), true);
  assert.equal(state.loading, true);
  assert.equal(beginAuthentication(state), false);

  finishAuthentication(state);
  assert.equal(state.loading, false);
});
