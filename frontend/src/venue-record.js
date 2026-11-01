// [SCRUM-92 | Elnathan] The rules behind the Venue Staff venue form: what a
// new form holds, how input is checked and sent, and how the backend's
// replies become messages and rows. Plain functions with no requests, so the
// tests can call them directly; app.js does the requests.

export const NO_CHANGES = "No changes to save.";

const LIST_FIELDS = ["facilities", "supported_layouts"];
const OPTIONAL_TEXT_FIELDS = ["accessibility", "operating_hours"];
const FORM_FIELDS = ["name", "location", "capacity", ...LIST_FIELDS, ...OPTIONAL_TEXT_FIELDS, "setup_time_mins", "turnaround_time_mins"];

// D2: a new form has every field empty, except setup 30 and turnaround 45,
// the backend's defaults. Form values are text, as the inputs give them.
export function emptyVenueForm() {
  return {
    name: "", location: "", capacity: "",
    facilities: "", accessibility: "", supported_layouts: "", operating_hours: "",
    setup_time_mins: "30", turnaround_time_mins: "45",
  };
}

// AC2: the edit form, filled from what GET /venues/{id} returned.
export function venueFormFrom(venue) {
  const text = value => (value === null || value === undefined ? "" : String(value));
  return {
    name: text(venue.name), location: text(venue.location), capacity: text(venue.capacity),
    facilities: (venue.facilities || []).join(", "),
    accessibility: text(venue.accessibility),
    supported_layouts: (venue.supported_layouts || []).join(", "),
    operating_hours: text(venue.operating_hours),
    setup_time_mins: text(venue.setup_time_mins), turnaround_time_mins: text(venue.turnaround_time_mins),
  };
}

// D2: "Projector, Stage" becomes ["Projector", "Stage"]; empty items are dropped.
export function parseList(text) {
  return String(text ?? "").split(",").map(item => item.trim()).filter(Boolean);
}

// A whole number typed as text, or null if it isn't one ("1.5", "", "abc").
function wholeNumber(value) {
  const text = String(value ?? "").trim();
  return /^-?\d+$/.test(text) ? Number(text) : null;
}

// D4: the backend's rules, checked before anything is sent.
export function validateVenueForm(form) {
  const errors = {};
  if (!String(form.name ?? "").trim()) errors.name = "Venue name is required";
  if (!String(form.location ?? "").trim()) errors.location = "Location is required";
  const capacity = wholeNumber(form.capacity);
  if (capacity === null || capacity <= 0) errors.capacity = "Enter a positive whole number";
  for (const field of ["setup_time_mins", "turnaround_time_mins"]) {
    const minutes = wholeNumber(form[field]);
    if (minutes === null || minutes < 0) errors[field] = "Enter whole minutes, 0 or more";
  }
  return errors;
}

// AC1: the POST /venues body, with all nine fields. Lists are sent as lists,
// numbers as numbers, and an empty optional text field as null.
export function createPayload(form) {
  return {
    name: String(form.name).trim(),
    location: String(form.location).trim(),
    capacity: wholeNumber(form.capacity),
    facilities: parseList(form.facilities),
    accessibility: String(form.accessibility ?? "").trim() || null,
    supported_layouts: parseList(form.supported_layouts),
    operating_hours: String(form.operating_hours ?? "").trim() || null,
    setup_time_mins: wholeNumber(form.setup_time_mins),
    turnaround_time_mins: wholeNumber(form.turnaround_time_mins),
  };
}

// D3: the PATCH /venues/{id} body. Only the fields that differ from the
// stored venue are sent, and a cleared optional field is sent as null.
export function changedFields(form, venue) {
  const next = createPayload(form);
  const changes = {};
  for (const field of FORM_FIELDS) {
    if (LIST_FIELDS.includes(field)) {
      if (JSON.stringify(next[field]) !== JSON.stringify(venue[field] || [])) changes[field] = next[field].length ? next[field] : null;
    } else if (OPTIONAL_TEXT_FIELDS.includes(field)) {
      if (next[field] !== (venue[field] || null)) changes[field] = next[field];
    } else if (next[field] !== venue[field]) {
      changes[field] = next[field];
    }
  }
  return changes;
}

// D4: a failed save, as field errors and one message for the form. A 422
// names its fields; a 409 or anything else has a single detail message.
export function formErrorsFrom(response) {
  const detail = response?.data?.detail;
  if (Array.isArray(detail)) {
    const fields = {};
    const others = [];
    for (const error of detail) {
      const field = error.loc?.at(-1);
      const message = String(error.msg || "").replace(/^Value error, /, "");
      if (FORM_FIELDS.includes(field)) fields[field] = message;
      else others.push(field ? `${field}: ${message}` : message);
    }
    return { fields, message: others.length ? others.join(" ") : null };
  }
  if (typeof detail === "string") return { fields: {}, message: detail };
  return { fields: {}, message: `The venue could not be saved (status ${response?.status}).` };
}

// AC3, D5: the bookings PATCH /venues/{id} newly flagged, with the venue's
// name (from the response), the event, the period and the clash reason.
export function flaggedBookingRows(data) {
  return (data?.flagged_bookings || []).map(booking => ({
    id: booking.booking_id ?? booking.id,
    venueName: data.venue?.name ?? "",
    eventTitle: booking.event_title,
    start: booking.booking_start,
    end: booking.booking_end,
    reason: booking.conflict_flag?.reason ?? "",
  }));
}

// AC4, D6: the bookings that still held the venue when it was retired.
export function affectedBookingRows(data) {
  return (data?.affected_bookings || []).map(booking => ({
    id: booking.booking_id,
    eventTitle: booking.event_title,
    start: booking.booking_start,
    end: booking.booking_end,
    status: booking.status,
  }));
}

// AC5, D7: "30 min setup, 45 min turnaround".
export function bufferSummary(venue) {
  return `${venue.setup_time_mins} min setup, ${venue.turnaround_time_mins} min turnaround`;
}
