// Sprint 5 equipment catalogue on /technical: SCRUM-41 (the catalogue) and SCRUM-42
// (update an item, record units unavailable). The component is called directly,
// with apiRequest stubbed, and the markup is read as text.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { equipmentCatalogue } from "../src/equipment-catalogue.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
const panel = html.slice(html.indexOf('x-data="equipmentCatalogue()"'), html.indexOf("<!-- Technical Support: the equipment worklist"));

const microphone = { id: 1, equipment_type: "Audio", name: "Microphone", total_quantity: 10, notes: "Spare batteries" };
const period = { id: 5, item_id: 1, units: 2, start_at: "2099-03-01T01:00:00", end_at: "2099-03-01T05:00:00", reason: "Repairs" };

// The parent page's helpers, as far as this component uses them.
function component(replies = {}) {
  const state = equipmentCatalogue();
  const sent = [];
  state.dialogErrorMessage = response => response.data.detail;
  state.inputToDate = value => (value ? new Date(`${value}:00+08:00`) : null); // Singapore time
  state.formatUtc = value => `utc ${value}`;
  state.apiRequest = async (path, options = {}) => {
    const method = options.method || "GET";
    sent.push([method, path, options.body]);
    const reply = replies[`${method} ${path}`];
    if (reply) return typeof reply === "function" ? reply(options.body) : reply;
    if (method === "GET" && path === "/equipment/items") return { ok: true, status: 200, data: { items: [microphone] } };
    if (method === "GET") return { ok: true, status: 200, data: { periods: [period] } };
    return { ok: true, status: 200, data: {} };
  };
  return { state, sent };
}

test("TC-4121 the catalogue loads on /technical, for Technical Support only", async () => {
  const { state, sent } = component();
  await state.init();
  assert.deepEqual(sent, [["GET", "/equipment/items", undefined]]);
  assert.equal(state.catalogue.loaded, true);
  assert.deepEqual(state.catalogue.items.map(item => item.name), ["Microphone"]);
  const place = html.indexOf("<!-- SCRUM-41 equipment catalogue workspace -->");
  assert.ok(place > 0 && place < html.indexOf('x-data="equipmentCatalogue()"'));
  assert.match(html, /<template x-if="currentPath === '\/technical' && user\.role === 'Technical Support'"><section class="queue-panel equipment-catalogue" x-data="equipmentCatalogue\(\)"/);
  assert.match(main, /import \{ equipmentCatalogue \} from "\.\/equipment-catalogue\.js";/);
  assert.match(main, /Alpine\.data\("equipmentCatalogue", equipmentCatalogue\);/);
});

test("TC-4122 a refused load shows the permission state, and a failed refresh keeps the list", async () => {
  const refused = component({ "GET /equipment/items": { ok: false, status: 403, data: { detail: "Only Technical Support Staff can manage the equipment catalogue" } } });
  await refused.state.init();
  assert.equal(refused.state.catalogue.denied, true);
  assert.match(refused.state.catalogue.error, /Only Technical Support/);

  const { state } = component();
  await state.init();
  state.apiRequest = async () => ({ ok: false, status: 503, data: { detail: "Try later" } });
  await state.loadCatalogue();
  assert.equal(state.catalogue.items.length, 1);
  assert.equal(state.catalogue.error, "Try later");
  assert.equal(state.catalogue.denied, false);
});

test("TC-4123 adding an item checks the fields first, so a bad form sends nothing", async () => {
  const { state, sent } = component();
  await state.init();
  sent.length = 0;
  state.startCatalogueAdd();
  const tries = [
    [{ equipment_type: " ", name: "Mic", total_quantity: "1" }, "equipment_type"],
    [{ equipment_type: "x".repeat(101), name: "Mic", total_quantity: "1" }, "equipment_type"],
    [{ equipment_type: "Audio", name: "", total_quantity: "1" }, "name"],
    [{ equipment_type: "Audio", name: "x".repeat(256), total_quantity: "1" }, "name"],
    [{ equipment_type: "Audio", name: "Mic", total_quantity: "0" }, "total_quantity"],
    [{ equipment_type: "Audio", name: "Mic", total_quantity: "1.5" }, "total_quantity"],
    [{ equipment_type: "Audio", name: "Mic", total_quantity: "2147483648" }, "total_quantity"],
  ];
  for (const [form, field] of tries) {
    state.catalogue.form = { notes: "", ...form };
    await state.saveCatalogueItem();
    assert.ok(state.catalogue.errors[field], field);
  }
  assert.deepEqual(sent, []);
});

test("TC-4124 adding an item sends it, closes the form, refreshes the list and says so", async () => {
  const { state, sent } = component();
  await state.init();
  sent.length = 0;
  state.startCatalogueAdd();
  Object.assign(state.catalogue.form, { equipment_type: " Audio ", name: " Mic ", total_quantity: "2147483647", notes: " " });
  await state.saveCatalogueItem();
  assert.deepEqual(sent[0], ["POST", "/equipment/items", { equipment_type: "Audio", name: "Mic", total_quantity: 2147483647, notes: null }]);
  assert.deepEqual(sent[1], ["GET", "/equipment/items", undefined]);
  assert.equal(state.catalogue.form, null);
  assert.match(state.catalogue.message, /Mic/);
});

test("TC-4125 the backend's sentence is shown and the form stays open with what was typed", async () => {
  const { state } = component({ "POST /equipment/items": { ok: false, status: 409, data: { detail: "'Mic' is already in the catalogue as Audio. Update that item instead." } } });
  await state.init();
  state.startCatalogueAdd();
  Object.assign(state.catalogue.form, { equipment_type: "Audio", name: "Mic", total_quantity: "2" });
  await state.saveCatalogueItem();
  assert.match(state.catalogue.errors.form, /already in the catalogue/);
  assert.equal(state.catalogue.form.name, "Mic");
  assert.equal(state.catalogue.saving, false);
});

test("TC-4126 the catalogue markup has labels, a live status, and a table that works as cards on a phone", () => {
  for (const id of ["catalogue-type", "catalogue-name", "catalogue-total", "catalogue-notes"]) {
    assert.match(panel, new RegExp(`<label for="${id}"`), id);
    assert.match(panel, new RegExp(`id="${id}"`), id);
  }
  assert.match(panel, /role="status"[^>]*x-text="catalogue\.message"/);
  for (const label of ["Name", "Type", "Total", "Notes", "Actions"]) assert.match(panel, new RegExp(`<td data-label="${label}"`), label);
  // On a phone the first cell is the card's title and shows no label, so the name goes first.
  assert.match(panel, /<tr><td data-label="Name" class="equipment-cell-type" x-text="item\.name"><\/td><td data-label="Type"/);
  assert.match(panel, /No equipment in the catalogue yet/);
  assert.match(panel, /x-show="catalogue\.denied"/);
});

test("TC-4221 editing fills the form and sends only what changed", async () => {
  const { state, sent } = component();
  await state.init();
  sent.length = 0;
  state.startCatalogueEdit(microphone);
  assert.deepEqual({ ...state.catalogue.form }, { equipment_type: "Audio", name: "Microphone", total_quantity: "10", notes: "Spare batteries" });
  state.catalogue.form.total_quantity = "7";
  state.catalogue.form.notes = "";
  await state.saveCatalogueItem();
  assert.deepEqual(sent[0], ["PATCH", "/equipment/items/1", { total_quantity: 7, notes: null }]);
  assert.equal(state.catalogue.form, null);
});

test("TC-4222 saving an edit with no changes sends nothing and says so", async () => {
  const { state, sent } = component();
  await state.init();
  sent.length = 0;
  state.startCatalogueEdit(microphone);
  await state.saveCatalogueItem();
  assert.deepEqual(sent, []);
  assert.equal(state.catalogue.errors.form, "No changes to save.");
});

test("TC-4223 opening an item's unavailable units lists its periods", async () => {
  const { state, sent } = component();
  await state.init();
  sent.length = 0;
  await state.openCataloguePeriods(microphone);
  assert.deepEqual(sent, [["GET", "/equipment/items/1/unavailability", undefined]]);
  assert.equal(state.catalogue.periods.item.name, "Microphone");
  assert.deepEqual(state.catalogue.periods.rows.map(row => row.units), [2]);
});

test("TC-4224 recording unavailable units checks the numbers and times before sending", async () => {
  const { state, sent } = component();
  await state.init();
  await state.openCataloguePeriods(microphone);
  sent.length = 0;
  const good = { units: "2", start_at: "2099-03-01T09:00", end_at: "2099-03-01T13:00", reason: "" };
  const tries = [
    [{ units: "0" }, "units"],
    [{ units: "1.5" }, "units"],
    [{ units: "11" }, "units"], // the item only has 10
    [{ start_at: "" }, "start"],
    [{ end_at: "" }, "end"],
    [{ end_at: "2099-03-01T09:00" }, "end"],
    [{ end_at: "2099-03-01T08:00" }, "end"],
  ];
  for (const [change, field] of tries) {
    state.catalogue.periods.form = { ...good, ...change };
    await state.savePeriod();
    assert.ok(state.catalogue.periods.errors[field], field);
  }
  assert.deepEqual(sent, []);
});

test("TC-4225 the times typed are Singapore time and are sent as UTC, and the list refreshes", async () => {
  const { state, sent } = component();
  await state.init();
  await state.openCataloguePeriods(microphone);
  sent.length = 0;
  state.catalogue.periods.form = { units: "10", start_at: "2099-03-01T09:00", end_at: "2099-03-01T13:00", reason: " Repairs " };
  await state.savePeriod();
  assert.deepEqual(sent[0], ["POST", "/equipment/items/1/unavailability", { units: 10, start_at: "2099-03-01T01:00:00.000Z", end_at: "2099-03-01T05:00:00.000Z", reason: "Repairs" }]);
  assert.deepEqual(sent[1], ["GET", "/equipment/items/1/unavailability", undefined]);
  assert.equal(state.catalogue.periods.form.units, "");
  assert.match(state.catalogue.periods.message, /recorded/i);
});

test("TC-4226 a refused period shows the backend's sentence and keeps what was typed", async () => {
  const { state } = component({ "POST /equipment/items/1/unavailability": { ok: false, status: 400, data: { detail: "This item only has 10 units in total, so 11 can't be unavailable." } } });
  await state.init();
  await state.openCataloguePeriods(microphone);
  state.catalogue.periods.form = { units: "3", start_at: "2099-03-01T09:00", end_at: "2099-03-01T13:00", reason: "" };
  await state.savePeriod();
  assert.match(state.catalogue.periods.errors.form, /only has 10 units/);
  assert.equal(state.catalogue.periods.form.units, "3");
});

test("TC-4227 a period can be removed, and a failed removal says why", async () => {
  const { state, sent } = component();
  await state.init();
  await state.openCataloguePeriods(microphone);
  sent.length = 0;
  await state.removePeriod(period);
  assert.deepEqual(sent, [["DELETE", "/equipment/items/1/unavailability/5", undefined], ["GET", "/equipment/items/1/unavailability", undefined]]);

  const failing = component({ "DELETE /equipment/items/1/unavailability/5": { ok: false, status: 404, data: { detail: "Unavailable period not found" } } });
  await failing.state.init();
  await failing.state.openCataloguePeriods(microphone);
  await failing.state.removePeriod(period);
  assert.equal(failing.state.catalogue.periods.errors.form, "Unavailable period not found");
});

test("TC-4228 the unavailable units markup has labels and says the times are Singapore time", () => {
  for (const id of ["period-units", "period-start", "period-end", "period-reason"]) {
    assert.match(panel, new RegExp(`<label for="${id}"`), id);
    assert.match(panel, new RegExp(`id="${id}"`), id);
  }
  assert.match(panel, /Singapore time/);
  assert.match(panel, /formatUtc\(row\.start_at\)/);
  assert.match(panel, /@click="removePeriod\(row\)"/);
});

test("TC-4127 closing the form returns focus to Add item, so keyboard users don't lose their place", async () => {
  const { state } = component();
  await state.init();
  let focused = 0;
  state.$nextTick = callback => callback();
  // Like Alpine: $refs belongs to the clicked button, so it's empty once the form has gone.
  Object.defineProperty(state, "$refs", { get: () => (state.catalogue.form ? { catalogueAdd: { focus: () => { focused += 1; } } } : undefined) });
  state.startCatalogueAdd();
  state.cancelCatalogueForm();
  assert.equal(focused, 1);
  state.startCatalogueAdd();
  Object.assign(state.catalogue.form, { equipment_type: "Audio", name: "Mic", total_quantity: "2" });
  await state.saveCatalogueItem();
  assert.equal(focused, 2);
  assert.match(panel, /x-ref="catalogueAdd"/);
});

test("TC-4229 closing the unavailable units section returns focus to that item's button", async () => {
  const { state } = component();
  await state.init();
  const looked = [];
  let focused = 0;
  state.$nextTick = callback => callback();
  // Like Alpine: $root belongs to the clicked Close button, so it's empty once the section has gone.
  Object.defineProperty(state, "$root", { get: () => (state.catalogue.periods ? { querySelector: selector => { looked.push(selector); return { focus: () => { focused += 1; } }; } } : undefined) });
  await state.openCataloguePeriods(microphone);
  state.closeCataloguePeriods();
  assert.equal(state.catalogue.periods, null);
  assert.deepEqual(looked, ['[data-periods-item="1"]']);
  assert.equal(focused, 1);
  assert.match(panel, /:data-periods-item="item\.id"[^>]*@click="openCataloguePeriods\(item\)"/);
});

test("TC-4230 removing a period moves focus to the section heading, because the Remove button goes away", async () => {
  // Like Alpine: $refs belongs to the clicked Remove button, so it's empty once the row has gone.
  let rowGone = false;
  const { state } = component({ "DELETE /equipment/items/1/unavailability/5": () => { rowGone = true; return { ok: true, status: 200, data: {} }; } });
  await state.init();
  let focused = 0;
  state.$nextTick = callback => callback();
  Object.defineProperty(state, "$refs", { get: () => (rowGone ? undefined : { periodsHeading: { focus: () => { focused += 1; } } }) });
  await state.openCataloguePeriods(microphone);
  await state.removePeriod(period);
  assert.equal(focused, 1);
  assert.match(panel, /<h3 id="equipment-periods-title" tabindex="-1" x-ref="periodsHeading"/);

  const failing = component({ "DELETE /equipment/items/1/unavailability/5": { ok: false, status: 404, data: { detail: "Unavailable period not found" } } });
  await failing.state.init();
  let refusedFocus = 0;
  failing.state.$nextTick = callback => callback();
  failing.state.$refs = { periodsHeading: { focus: () => { refusedFocus += 1; } } };
  await failing.state.openCataloguePeriods(microphone);
  await failing.state.removePeriod(period);
  assert.equal(refusedFocus, 0, "a refused removal keeps the button, so focus stays on it");
});
