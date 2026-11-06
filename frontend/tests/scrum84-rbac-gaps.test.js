import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

// [SCRUM-84] What scrum17-rbac.test.js doesn't check: that the page itself is gated, and that requests carry their number.
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

// The roles PR #18 delivered. A fixed list on purpose: later stories add workspaces with their own gates.
const WORKSPACE_GATES = [
  "currentPath === '/events' && user.role === 'Organiser'",
  "currentPath === '/events/new' && user.role === 'Organiser'",
  "currentPath === '/coordinator/events' && user.role === 'Coordinator'",
  "currentPath === '/coordinator/all-events' && user.role === 'Coordinator'",
  "currentPath === '/lead/events' && user.role === 'Event Coordinator Lead'",
  "currentPath === '/venue' && isVenueStaff()",
  "currentPath === '/technical' && user.role === 'Technical Support'",
  "currentPath === '/attendee' && user.role === 'Attendee'",
];

test("TC-8410 each role's workspace is gated by that role, not by its path alone", () => {
  for (const gate of WORKSPACE_GATES) assert.ok(html.includes(`<template x-if="${gate}`), gate);
});

test("TC-8411 the access-denied page exists and follows the accessDenied state", () => {
  assert.match(html, /x-(if|show)="accessDenied[^"]*"/);
  assert.match(html, /Access denied/);
});

test("TC-8412 the Organiser and Coordinator lists both show each request's number", () => {
  const between = (from, to) => html.slice(html.indexOf(from), html.indexOf(to, html.indexOf(from)));
  const organiser = between("currentPath === '/events' && user.role === 'Organiser'", "currentPath === '/events/new'");
  const assigned = html.slice(html.indexOf("currentPath === '/coordinator/events' && user.role === 'Coordinator'"));
  const identifier = '<span class="request-id" x-text="`#${event.id}`"></span>';
  assert.ok(organiser.includes(identifier), "the Organiser's list");
  assert.ok(assigned.includes(identifier), "the Coordinator's list");
});
