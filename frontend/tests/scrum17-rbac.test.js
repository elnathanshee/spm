import assert from "node:assert/strict";
import test from "node:test";

import { NAVIGATION, canAccess, defaultPathForRole, normalizeRole, requestedFrontendPath, resolveRolePath } from "../src/rbac.js";

const ROLES = {
  organiser: { display: "Organiser", path: "/events" },
  coordinator: { display: "Coordinator", path: "/coordinator/events" },
  lead: { display: "Event Coordinator Lead", path: "/lead/events" },
  "venue staff": { display: "Venue Staff", path: "/venue" },
  "technical support": { display: "Technical Support", path: "/technical" },
  attendee: { display: "Attendee", path: "/attendee" },
};

test("all current backend role values map to their frontend role and landing view", () => {
  for (const [backendRole, expected] of Object.entries(ROLES)) {
    assert.equal(normalizeRole(backendRole), expected.display);
    assert.equal(defaultPathForRole(backendRole), expected.path);
  }
});

test("each role sees only its own navigation functions", () => {
  for (const { display } of Object.values(ROLES)) {
    const visibleItems = NAVIGATION.filter((item) => canAccess(item.role, display));
    assert.ok(visibleItems.length > 0, `${display} should have a visible function`);
    assert.ok(visibleItems.every((item) => item.role === display));
    assert.equal(NAVIGATION.filter((item) => item.role !== display && canAccess(item.role, display)).length, 0);
  }
});

test("role-appropriate frontend views remain accessible", () => {
  for (const { display, path } of Object.values(ROLES)) {
    const result = resolveRolePath(path, display);
    assert.equal(result.accessDenied, false);
    assert.equal(result.currentPath, path);
  }
});

test("cross-role and unknown frontend views are explicitly denied", () => {
  for (const { display } of Object.values(ROLES)) {
    for (const item of NAVIGATION.filter((entry) => entry.role !== display)) {
      assert.deepEqual(resolveRolePath(item.path, display), {
        accessDenied: true,
        currentPath: "/access-denied",
        unbuiltRole: false,
      });
    }
    assert.equal(resolveRolePath("/not-a-real-function", display).accessDenied, true);
  }
});

test("Organiser and Attendee cannot enter the Coordinator planning view", () => {
  for (const externalRole of ["Organiser", "Attendee"]) {
    assert.equal(resolveRolePath("/coordinator/events", externalRole).accessDenied, true);
  }
});

test("only the Lead can enter assignment oversight", () => {
  assert.equal(resolveRolePath("/lead/events", "Event Coordinator Lead").accessDenied, false);
  for (const role of ["Organiser", "Coordinator", "Venue Staff", "Technical Support", "Attendee"]) {
    assert.equal(resolveRolePath("/lead/events", role).accessDenied, true);
  }
});

test("Admin is not a supported frontend application role", () => {
  assert.equal(NAVIGATION.some((item) => item.role === "Admin"), false);
  assert.equal(resolveRolePath("/events", "Admin").accessDenied, true);
  assert.equal(resolveRolePath("/lead/events", "admin").accessDenied, true);
});

test("a manually requested frontend route is retained for an explicit access decision", () => {
  assert.equal(requestedFrontendPath("#/coordinator/events"), "/coordinator/events");
  assert.equal(requestedFrontendPath(""), null);
  assert.equal(resolveRolePath(requestedFrontendPath("#/coordinator/events"), "Organiser").accessDenied, true);
});
