// SCRUM-92: unit tests for src/venue-record.js, the Venue Staff venue form's
// rules. Expected values come from SCRUM-92's criteria and the PO's decisions
// (D2 inputs, D3 edit, D4 errors, D5 flagged bookings, D6 retire, D7 catalogue).
import assert from "node:assert/strict";
import test from "node:test";
import {
  NO_CHANGES,
  affectedBookingRows,
  bufferSummary,
  changedFields,
  createPayload,
  emptyVenueForm,
  flaggedBookingRows,
  formErrorsFrom,
  parseList,
  validateVenueForm,
  venueFormFrom,
} from "../src/venue-record.js";

const fullForm = () => ({
  name: " Harbour Ballroom ", location: "Block B, Level 2", capacity: "250",
  facilities: "Projector, Stage, WiFi", accessibility: "Step-free entrance; hearing loop",
  supported_layouts: "Theatre, Banquet", operating_hours: "Mon-Fri 08:00-22:00",
  setup_time_mins: "20", turnaround_time_mins: "60",
});

// A venue as GET /venues/{id} returns it.
const storedVenue = () => ({
  id: 7, name: "Grand Hall", location: "Block A", capacity: 120,
  facilities: ["Projector", "Stage"], accessibility: "Ramp", supported_layouts: [],
  operating_hours: null, setup_time_mins: 30, turnaround_time_mins: 45,
  is_active: true, created_at: "2026-10-01T09:00:00",
});

test("TC-9201 (module) a full form becomes one nine-field body, lists as lists and numbers as numbers", () => {
  assert.deepEqual(createPayload(fullForm()), {
    name: "Harbour Ballroom", location: "Block B, Level 2", capacity: 250,
    facilities: ["Projector", "Stage", "WiFi"], accessibility: "Step-free entrance; hearing loop",
    supported_layouts: ["Theatre", "Banquet"], operating_hours: "Mon-Fri 08:00-22:00",
    setup_time_mins: 20, turnaround_time_mins: 60,
  });
  assert.deepEqual(validateVenueForm(fullForm()), {});
});

test("TC-9202 (module) a new form shows 30 and 45, and sends them if left unchanged", () => {
  const form = emptyVenueForm();
  assert.equal(form.setup_time_mins, "30");
  assert.equal(form.turnaround_time_mins, "45");
  for (const field of ["name", "location", "capacity", "facilities", "accessibility", "supported_layouts", "operating_hours"]) {
    assert.equal(form[field], "", field);
  }
  const body = createPayload({ ...form, name: "Hall", location: "Campus", capacity: "100" });
  assert.equal(body.setup_time_mins, 30);
  assert.equal(body.turnaround_time_mins, 45);
  assert.deepEqual(body.facilities, []);
  assert.equal(body.accessibility, null);
});

test("TC-9203 (module) blank name or location, capacity 0 and a negative setup time are caught", () => {
  const errors = validateVenueForm({ ...emptyVenueForm(), name: "   ", location: "", capacity: "0", setup_time_mins: "-5" });
  assert.deepEqual(Object.keys(errors).sort(), ["capacity", "location", "name", "setup_time_mins"]);
});

test("TC-9204 (module) capacity 1 and setup 0 are accepted; capacity 0, setup -1, capacity 1.5 and turnaround -1 are not", () => {
  const base = { ...emptyVenueForm(), name: "Hall", location: "Campus" };
  assert.deepEqual(validateVenueForm({ ...base, capacity: "1", setup_time_mins: "0", turnaround_time_mins: "0" }), {});
  assert.ok(validateVenueForm({ ...base, capacity: "0" }).capacity);
  assert.ok(validateVenueForm({ ...base, capacity: "1", setup_time_mins: "-1" }).setup_time_mins);
  assert.ok(validateVenueForm({ ...base, capacity: "1.5" }).capacity);
  assert.ok(validateVenueForm({ ...base, capacity: "1", turnaround_time_mins: "-1" }).turnaround_time_mins);
  assert.ok(validateVenueForm({ ...base, capacity: "1", setup_time_mins: "" }).setup_time_mins);
});

test("TC-9205 (module) the edit form is prefilled from the venue, and changing only capacity sends only capacity", () => {
  const form = venueFormFrom(storedVenue());
  assert.deepEqual(form, {
    name: "Grand Hall", location: "Block A", capacity: "120", facilities: "Projector, Stage",
    accessibility: "Ramp", supported_layouts: "", operating_hours: "",
    setup_time_mins: "30", turnaround_time_mins: "45",
  });
  assert.deepEqual(changedFields({ ...form, capacity: "150" }, storedVenue()), { capacity: 150 });
});

test("TC-9206 (module) an unchanged form, even retyped with extra spaces, sends nothing", () => {
  const form = venueFormFrom(storedVenue());
  assert.deepEqual(changedFields(form, storedVenue()), {});
  assert.deepEqual(changedFields({ ...form, name: "  Grand Hall ", facilities: "Projector ,Stage, " }, storedVenue()), {});
  assert.equal(NO_CHANGES, "No changes to save.");
});

test("TC-9207 (module) a 409 becomes the form's message; a 422 on a field becomes that field's error", () => {
  assert.deepEqual(formErrorsFrom({ ok: false, status: 409, data: { detail: "A venue named 'Grand Hall' already exists" } }),
    { fields: {}, message: "A venue named 'Grand Hall' already exists" });
  const invalid = { ok: false, status: 422, data: { detail: [
    { type: "value_error", loc: ["body", "capacity"], msg: "Value error, Capacity must be greater than 0" },
  ] } };
  assert.deepEqual(formErrorsFrom(invalid), { fields: { capacity: "Capacity must be greater than 0" }, message: null });
  const unknown = { ok: false, status: 422, data: { detail: [
    { type: "extra_forbidden", loc: ["body", "colour"], msg: "Extra inputs are not permitted" },
  ] } };
  assert.deepEqual(formErrorsFrom(unknown), { fields: {}, message: "colour: Extra inputs are not permitted" });
  assert.equal(formErrorsFrom({ ok: false, status: 500, data: {} }).message, "The venue could not be saved (status 500).");
});

test("TC-9208 (module) each flagged booking is listed with the venue, event, period and clash reason", () => {
  const reason = (other) => `Clashes with booking #${other} (2026-11-16 10:00 - 12:00) once 30m setup and 90m turnaround are included`;
  const rows = flaggedBookingRows({
    venue: { id: 7, name: "Grand Hall" },
    flagged_bookings: [
      { booking_id: 1, id: 1, venue_id: 7, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00", status: "Confirmed", conflict_flag: { conflicting_booking_id: 2, reason: reason(2), flagged_at: "2026-10-26T09:00:00" } },
      { booking_id: 2, id: 2, venue_id: 7, event_title: "Spring Fair", booking_start: "2026-11-16T13:30:00", booking_end: "2026-11-16T15:00:00", status: "Confirmed", conflict_flag: { conflicting_booking_id: 1, reason: reason(1), flagged_at: "2026-10-26T09:00:00" } },
    ],
  });
  assert.deepEqual(rows, [
    { id: 1, venueName: "Grand Hall", eventTitle: "Annual Gala", start: "2026-11-16T10:00:00", end: "2026-11-16T12:00:00", reason: reason(2) },
    { id: 2, venueName: "Grand Hall", eventTitle: "Spring Fair", start: "2026-11-16T13:30:00", end: "2026-11-16T15:00:00", reason: reason(1) },
  ]);
  assert.deepEqual(flaggedBookingRows({ venue: { name: "Grand Hall" }, flagged_bookings: [] }), []);
});

test("TC-9209 (module) each affected booking is listed with its event, period and status", () => {
  const rows = affectedBookingRows({ affected_bookings: [
    { booking_id: 4, event_id: 2, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00", status: "Confirmed" },
    { booking_id: 5, event_id: 3, event_title: "Spring Fair", booking_start: "2026-11-16T13:30:00", booking_end: "2026-11-16T15:00:00", status: "Pending" },
  ] });
  assert.deepEqual(rows, [
    { id: 4, eventTitle: "Annual Gala", start: "2026-11-16T10:00:00", end: "2026-11-16T12:00:00", status: "Confirmed" },
    { id: 5, eventTitle: "Spring Fair", start: "2026-11-16T13:30:00", end: "2026-11-16T15:00:00", status: "Pending" },
  ]);
  assert.deepEqual(affectedBookingRows({ affected_bookings: [] }), []);
});

test("TC-9211 (module) the buffers read as minutes of setup and turnaround", () => {
  assert.equal(bufferSummary({ setup_time_mins: 30, turnaround_time_mins: 45 }), "30 min setup, 45 min turnaround");
  assert.equal(bufferSummary({ setup_time_mins: 0, turnaround_time_mins: 90 }), "0 min setup, 90 min turnaround");
});

test("TC-9214 comma-separated lists are trimmed and empty items dropped", () => {
  assert.deepEqual(parseList(" Projector, ,Stage , WiFi,"), ["Projector", "Stage", "WiFi"]);
  assert.deepEqual(parseList(""), []);
  assert.deepEqual(parseList(" , "), []);
});

test("TC-9215 clearing an optional field sends null for it", () => {
  const form = { ...venueFormFrom(storedVenue()), facilities: " ", accessibility: "" };
  assert.deepEqual(changedFields(form, storedVenue()), { facilities: null, accessibility: null });
  const filled = { ...venueFormFrom(storedVenue()), supported_layouts: "Theatre", operating_hours: " 9am-5pm " };
  assert.deepEqual(changedFields(filled, storedVenue()), { supported_layouts: ["Theatre"], operating_hours: "9am-5pm" });
});

// TC-9224: what each function does with a missing or null value. The backend
// sends empty lists as [] and empty text as null; these pin the fallbacks for
// anything else (a field left out, a 422 entry without loc or msg).
test("TC-9224 (module) missing and null values fall back to empty text, empty lists and null", () => {
  const sparseVenue = { id: 9, name: "Hall", location: "Campus", capacity: 10, facilities: null, accessibility: null,
    setup_time_mins: 30, turnaround_time_mins: 45 };  // supported_layouts and operating_hours left out
  const nullLists = { ...storedVenue(), facilities: null, supported_layouts: null };
  const sparseForm = { name: "Hall", location: "Campus", capacity: "10", setup_time_mins: "30", turnaround_time_mins: "45" };
  const cases = [
    ["venueFormFrom: null and missing fields become empty text", venueFormFrom(sparseVenue), {
      name: "Hall", location: "Campus", capacity: "10", facilities: "", accessibility: "",
      supported_layouts: "", operating_hours: "", setup_time_mins: "30", turnaround_time_mins: "45" }],
    ["parseList(null)", parseList(null), []],
    ["parseList(undefined)", parseList(undefined), []],
    ["validateVenueForm({}): every required field is caught", Object.keys(validateVenueForm({})).sort(),
      ["capacity", "location", "name", "setup_time_mins", "turnaround_time_mins"]],
    ["createPayload: missing optional fields send [] and null", createPayload(sparseForm), {
      name: "Hall", location: "Campus", capacity: 10, facilities: [], accessibility: null,
      supported_layouts: [], operating_hours: null, setup_time_mins: 30, turnaround_time_mins: 45 }],
    ["changedFields: a stored null list and an empty form field are the same", changedFields(venueFormFrom(nullLists), nullLists), {}],
    ["changedFields: a stored null list and a filled form field differ",
      changedFields({ ...venueFormFrom(nullLists), facilities: "Stage" }, nullLists), { facilities: ["Stage"] }],
    ["formErrorsFrom: a 422 entry without loc is shown as its message; one without msg gives an empty field error",
      formErrorsFrom({ ok: false, status: 422, data: { detail: [
        { type: "dict_type", msg: "Value error, Input should be a valid dictionary" },
        { type: "missing", loc: ["body", "capacity"] },
      ] } }), { fields: { capacity: "" }, message: "Input should be a valid dictionary" }],
    ["flaggedBookingRows(undefined)", flaggedBookingRows(undefined), []],
    ["flaggedBookingRows({})", flaggedBookingRows({}), []],
    ["flaggedBookingRows: no booking_id, venue or conflict_flag",
      flaggedBookingRows({ flagged_bookings: [{ id: 3, event_title: "Annual Gala", booking_start: "2026-11-16T10:00:00", booking_end: "2026-11-16T12:00:00" }] }),
      [{ id: 3, venueName: "", eventTitle: "Annual Gala", start: "2026-11-16T10:00:00", end: "2026-11-16T12:00:00", reason: "" }]],
    ["affectedBookingRows(undefined)", affectedBookingRows(undefined), []],
    ["affectedBookingRows({})", affectedBookingRows({}), []],
  ];
  for (const [label, actual, expected] of cases) assert.deepEqual(actual, expected, label);
});
