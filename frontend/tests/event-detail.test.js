import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { normalizeRole, resolveRolePath } from '../src/rbac.js';
import { resetSignedOutState } from '../src/session-state.js';
function app(role = 'Coordinator') {
 let factory;
 const source = readFileSync(new URL('../app.js', import.meta.url), 'utf8');
 vm.runInNewContext(source.trimStart().replace(/^const API_BASE.*$/m, 'const API_BASE = "";').replace(/^import[\s\S]*?from ".*?";/gm, ''), {
 NAVIGATION: [], normalizeRoleName: normalizeRole, resolveRolePath, resetSignedOutState, requestedFrontendPath: () => null,
 document: { addEventListener: (_, fn) => fn() }, Alpine: { data: (_, fn) => factory = fn }, window: { location: {} }
 });
 const state = factory(); state.user = { user_id: 2, role }; return state;
}
const detail = { id: 7, title: '<script>Safe</script>', purpose: 'Purpose', description: 'Long\n  text '.repeat(500), proposed_start: '2099-01-01T10:00:00', expected_attendance: 0, venue_requirements: 'Hall', accessibility_needs: 'Ramp', equipment_requirements: 'Mic', registration_needs: 'Tickets', status: 'Submitted', organiser_id: 1, organiser: 'org', assigned_coordinator_id: 2, assigned_coordinator: 'coord', clarification_note: 'Latest' };
test('TC-8801 three entry points and one safe complete detail renderer preserve content', async () => {
 const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
 assert.equal((html.match(/@click(?:\.prevent)?="openEventDetail\(event.id\)"/g) || []).length, 4); // title links (Coordinator x2, Lead) + the Organiser's View details
 assert.match(html, /x-for="\[key, value\] in eventDetailEntries\(eventDetail.detail\)"/); assert.match(html, /x-text="eventFieldValue\(key, value\)"/); // UI_AUDIT F5: id, status and raw user ids are shown elsewhere
 // Coordinators and Leads also load the event's equipment once the details arrive
 const s = app(); s.apiRequest = async path => { if (path === '/events/7/equipment-requests') return { ok: true, data: { equipment_requests: [] } }; assert.equal(path, '/events/7'); return { ok: true, data: detail }; };
 await s.openEventDetail(7); assert.equal(s.eventDetail.detail, detail);
 for (const [key, value] of Object.entries(detail)) assert.ok(s.eventFieldValue(key, value).length);
 assert.equal(s.eventFieldValue('description', detail.description), detail.description);
 assert.equal(s.eventFieldValue('expected_attendance', 0), '0'); assert.equal(s.eventFieldValue('x', null), 'Not provided');
 assert.equal(s.eventFieldValue('x', { nested: [1, false] }), JSON.stringify({ nested: [1, false] }, null, 2));
});
test('TC-8802 role, assignment and status determine actions, including handler checks', async () => {
 for (const role of ['Coordinator', 'lead', 'Organiser']) for (const assigned of [2, 3, null]) for (const status of ['Submitted', 'Under Review', 'Returned', 'Approved']) {
 const s = app(role); const event = { ...detail, assigned_coordinator_id: assigned, status }; s.eventDetail = { id: 7, detail: event };
 const eligible = role === 'Coordinator' && assigned === 2 && ['Submitted', 'Under Review'].includes(status);
 assert.equal(s.canDecideEvent(event), eligible); assert.equal(s.canDecideEvent(event, 'review'), eligible && status === 'Submitted');
 if (!eligible) { s.apiRequest = () => assert.fail('forbidden action'); await s.handleApprove(7); s.openDecision(event, 'return'); assert.equal(s.modal.open, false); }
 }
});
for (const action of ['review', 'approve', 'return', 'reject']) test(`TC-8803 ${action}: endpoint, payload, busy guard and successful refresh`, async () => {
 const s = app(); s.eventDetail = { id: 7, detail }; s.currentPath = '/coordinator/all-events';
 let finish; const calls = []; let assigned = 0; let all = 0;
 s.fetchAssignedEvents = async () => assigned++; s.fetchAllCoordinatorEvents = async () => all++;
 s.apiRequest = (path, options) => { calls.push([path, options]); return options ? new Promise(resolve => finish = resolve) : Promise.resolve({ ok: true, data: { ...detail, status: 'Under Review' } }); };
 const pending = s.decideEvent(7, action, '  Reason  '); await s.decideEvent(7, action, 'Reason'); assert.equal(calls.length, 1);
 assert.equal(calls[0][0], `/events/7/${action}`); assert.equal(calls[0][1].method, 'POST');
 assert.equal(calls[0][1].body?.reason, ['return', 'reject'].includes(action) ? 'Reason' : undefined);
 finish({ ok: true, data: { id: 7, status: 'Under Review' } }); await pending;
 assert.equal(assigned, 1); assert.equal(all, 1); assert.equal(calls.length, 2); assert.equal(s.eventDetail.detail.title, detail.title); assert.equal(s.decisionBusy, false);
});
test('TC-8804 reason required and failures preserve input with no false updates', async () => {
 for (const kind of ['return', 'reject']) for (const status of [0, 400, 403, 404, 422, 500]) {
 const s = app(); s.eventDetail = { id: 7, detail }; s.openDecision(detail, kind);
 s.apiRequest = () => assert.fail('blank reason submitted'); await s.submitModal(); assert.ok(s.fieldErrors.reason);
 s.decisionForm.reason = 'Keep reason'; s.fetchAssignedEvents = async () => {};
 s.apiRequest = async (_, options) => options ? { ok: false, status } : { ok: true, data: detail };
 await s.submitModal(); assert.equal(s.decisionForm.reason, 'Keep reason'); assert.equal(s.modal.open, true); assert.equal(s.eventDetail.detail.status, 'Submitted'); assert.equal(s.decisionBusy, false);
 }
});
test('TC-8805 detail loading, errors, retry and Lead read-only', async () => {
 for (const status of [0, 403, 404]) {
 const s = app('lead'); let finish; s.apiRequest = () => new Promise(resolve => finish = resolve);
 const pending = s.openEventDetail(7); assert.equal(s.eventDetail.loading, true); assert.equal(s.eventDetail.detail, null);
 finish({ ok: false, status }); await pending; assert.equal(s.eventDetail.loading, false); assert.ok(s.eventDetail.error);
 if (status === 403) assert.match(s.eventDetail.error, /permission/); if (status === 404) assert.match(s.eventDetail.error, /not found/);
 s.apiRequest = async () => ({ ok: true, data: detail }); await s.openEventDetail(7); assert.equal(s.canDecideEvent(s.eventDetail.detail), false);
 }
});
test('TC-8806 late detail responses cannot overwrite new event, close, navigation or session', async () => {
 for (const change of ['event', 'close', 'navigate', 'logout', 'session']) {
 const s = app(); let finish; s.apiRequest = () => new Promise(resolve => finish = resolve);
 const pending = s.openEventDetail(7);
 if (change === 'event') { s.apiRequest = async () => ({ ok: true, data: { ...detail, id: 8 } }); await s.openEventDetail(8); }
 if (change === 'close') s.closeEventDetail();
 if (change === 'navigate') { s.fetchAllCoordinatorEvents = () => {}; s.navigate('/coordinator/all-events'); }
 if (change === 'logout') resetSignedOutState(s);
 if (change === 'session') s.user = { user_id: 3, role: 'Coordinator' };
 finish({ ok: true, data: detail }); await pending;
 assert.notEqual(s.eventDetail?.detail?.id, 7);
 }
});
test('TC-8807 late action after logout cannot refresh or notify a new session', async () => {
 const s = app(); s.eventDetail = { id: 7, detail }; let finish;
 s.apiRequest = () => new Promise(resolve => finish = resolve); s.fetchAssignedEvents = () => assert.fail('old refresh');
 const pending = s.handleApprove(7); resetSignedOutState(s); s.user = { user_id: 3, role: 'Coordinator' };
 finish({ ok: true }); await pending; assert.equal(s.eventDetail, null); assert.equal(s.alert, null);
});

test('TC-8808 HTTP failures show in the current decision dialog; 401 clears private detail', async () => {
 const s = app(); s.eventDetail = { id: 7, detail }; s.openDecision(detail, 'reject'); s.decisionForm.reason = 'Keep';
 s.apiRequest = async () => ({ ok: false, status: 500, data: { detail: 'Service unavailable' } });
 await s.submitModal(); assert.equal(s.fieldErrors.form, 'Service unavailable'); assert.equal(s.eventDetail.actionError, 'Service unavailable');
 s.handleHttpError(401, { detail: 'Session expired' }, '/events/7'); assert.equal(s.eventDetail, null); assert.equal(s.modal.open, false);
});
test('TC-8809 queue actions reuse assigned membership and stale queue refreshes are discarded', async () => {
 const s = app(); s.assignedEvents = [{ id: 7, status: 'Submitted' }];
 assert.equal(s.canDecideEvent(s.assignedEvents[0]), true);
 assert.equal(s.canDecideEvent({ ...detail, assigned_coordinator_id: null }), false);
 for (const method of ['fetchAssignedEvents', 'fetchAllCoordinatorEvents']) {
 s.user = { user_id: 2, role: 'Coordinator' };
 let finish; s.apiRequest = () => new Promise(resolve => finish = resolve);
 const pending = s[method](); resetSignedOutState(s); finish({ ok: true, data: [detail] }); await pending;
 assert.equal(s.assignedEvents.length, 0); assert.equal(s.allCoordinatorEvents.length, 0);
 }
});

test('TC-8810 a queue decision never replaces a different open request', async () => {
 const s = app(); s.assignedEvents = [{ id: 7, status: 'Submitted' }];
 const other = { id: 8, detail: { ...detail, id: 8 } }; s.eventDetail = other;
 s.fetchAssignedEvents = async () => {};
 s.apiRequest = async (path, options) => { assert.equal(path, '/events/7/approve'); assert.equal(options.method, 'POST'); return { ok: true }; };
 await s.handleApprove(7); assert.equal(s.eventDetail, other); assert.equal(s.eventDetail.detail.id, 8);
});


// Use Alpine's installed runtime, rather than plain objects, to exercise proxy identity.
const alpineSource = readFileSync(new URL('../node_modules/alpinejs/dist/module.esm.js', import.meta.url), 'utf8');
const alpineReactive = vm.runInNewContext(alpineSource.replace(/export \{[\s\S]*?\};/, '') + '\nAlpine.reactive;', {
 MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } },
 window: { Element: class {} }, document: { createElement: () => ({}) },
 queueMicrotask, setTimeout, clearTimeout, console,
});
for (const status of [200, 0, 403, 404]) test(`TC-8811 Alpine reactive detail clears loading and renders success/error (${status})`, async () => {
 const s = alpineReactive(app()); s.currentPath = '/coordinator/all-events'; let finish;
 s.apiRequest = path => { if (path === '/events/7/equipment-requests') return Promise.resolve({ ok: true, data: { equipment_requests: [] } }); assert.equal(path, '/events/7'); return new Promise(resolve => finish = resolve); };
 const pending = s.openEventDetail(7);
 assert.equal(s.eventDetail.loading, true); assert.equal(s.eventDetail.detail, null);
 finish({ ok: status === 200, status, data: detail }); await pending;
 assert.equal(s.eventDetail.loading, false);
 if (status === 200) { assert.equal(s.eventDetail.detail.title, detail.title); assert.equal(s.canDecideEvent(s.eventDetail.detail), true); }
 else { assert.equal(s.eventDetail.detail, null); assert.ok(s.eventDetail.error); }
});
test('TC-8812 Alpine reactive detail still discards a response after another request opens', async () => {
 const s = alpineReactive(app()); let finish;
 s.apiRequest = () => new Promise(resolve => finish = resolve);
 const pending = s.openEventDetail(7);
 s.apiRequest = async () => ({ ok: true, data: { ...detail, id: 8 } }); await s.openEventDetail(8);
 finish({ ok: true, data: detail }); await pending;
 assert.equal(s.eventDetail.detail.id, 8); assert.equal(s.eventDetail.loading, false);
});
