import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

// [SCRUM-84] What scrum17-rbac.test.js doesn't check: that the page itself is gated, and that requests carry their number.
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

// The eight workspaces at the Sprint 5 foundation. PR 18 delivered four of them; later stories added the rest.
// A fixed list on purpose: other streams add workspaces with their own gates.
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

test("TC-8414 the Organiser and Coordinator lists are built the same way", () => {
  const organiser = html.slice(html.indexOf("currentPath === '/events' && user.role === 'Organiser'"), html.indexOf("currentPath === '/events/new'"));
  const assigned = html.slice(html.indexOf("currentPath === '/coordinator/events' && user.role === 'Coordinator'"));
  const section = '<section class="queue-panel request-group" :class="[`request-group-${group.key}`, group.attention ?';
  const list = '<ul class="request-list"><template x-for="event in group.events" :key="event.id"><li class="request-row">';
  for (const part of [section, list]) {
    assert.ok(organiser.includes(part), `the Organiser's list: ${part}`);
    assert.ok(assigned.includes(part), `the Coordinator's list: ${part}`);
  }
});

test("TC-8412 the Organiser and Coordinator lists both show each request's number", () => {
  const between = (from, to) => html.slice(html.indexOf(from), html.indexOf(to, html.indexOf(from)));
  const organiser = between("currentPath === '/events' && user.role === 'Organiser'", "currentPath === '/events/new'");
  const assigned = html.slice(html.indexOf("currentPath === '/coordinator/events' && user.role === 'Coordinator'"));
  const identifier = '<span class="request-id" x-text="`#${event.id}`"></span>';
  assert.ok(organiser.includes(identifier), "the Organiser's list");
  assert.ok(assigned.includes(identifier), "the Coordinator's list");
});
