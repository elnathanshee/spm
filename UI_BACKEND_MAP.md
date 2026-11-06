# UI ↔ Backend map

What the ConnectSphere backend offers, who may use it, and how the frontend
(`frontend/index.html`, `frontend/app.js`, `frontend/src/*.js`) uses it.

The backend is the source of truth. Each rule cites its file and function. Anything
marked **⚠ Uncertain** could not be settled from the code alone; it is flagged
rather than guessed.

Written against branch `improve-ui` at commit `8738462`.

---

## 0. Conventions used by every endpoint

| Topic | Rule | Source |
|---|---|---|
| Authentication | A session cookie `connectsphere_session` (httpOnly, SameSite=Lax), set by `POST /auth/login`. A missing or invalid cookie → **401** `"Not authenticated"`. | `backend/auth.py` `login`, `get_current_user` |
| Supported roles | Stored role strings (case-insensitive, trimmed): `organiser`, `coordinator`, `lead`, `venue staff` / `venue_staff`, `technical support` / `technical_support`, `attendee`. Any other role → **403** `"This account does not have a supported application role"`. | `backend/auth.py` `SUPPORTED_APPLICATION_ROLES`, `require_supported_role` |
| Role names in this doc | Organiser = `organiser`, Coordinator = `coordinator`, Lead = `lead` (Event Coordinator Lead), Venue Staff = `venue_staff`, Tech Support = `technical support`, Attendee = `attendee`. | `backend/seed_users.py` `sample_users` |
| Validation errors | A Pydantic body or query error → **422**, with `detail` as a list of `{loc, msg, ...}`. | FastAPI default |
| Hold checks | `run_hold_checks` runs **before every request** on the events, notifications, venues and venue-holds routers. It stores `Expired` on lapsed holds and sends hold notices (§3). It does **not** run on the auth, coordinator, lead, equipment, calendar or suitability routers. | `backend/main.py` `HOLD_CHECKS`; `backend/venue_holds.py` `run_hold_checks` |
| Frontend error handling | 401 → signed out. 403 → generic "permission" alert. 422 → field errors from `detail[].loc`. 0/503 → "cannot reach backend". Other codes → `detail` shown as an alert. Dialogs pass `inlineErrors` to show the error inside the dialog instead. | `frontend/app.js` `apiRequest`, `handleHttpError`, `dialogErrorMessage` |
| Dev proxy | Vite proxies `/auth`, `/events`, `/venues`, `/coordinator`, `/lead`, `/notifications` to `127.0.0.1:8000`. **`/equipment-requests` is not proxied.** | `frontend/vite.config.js` |

---

## 1. Endpoints

Errors list only what the endpoint itself raises. Every authenticated endpoint can also
return 401/403 (§0) and 422 (bad body).

### 1.1 Auth — `backend/auth.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `POST /auth/login` (`login`) | anyone | `{username, password}`, both non-blank after trim | `{message, user_id, role, username, redirect_url}`; sets the cookie | 400 missing fields; 401 `Invalid username or password`; 403 unsupported role; 422 blank field | `login` |
| `GET /auth/me` (`current_session_user`) | any signed-in role | — | `{user_id, username, role}` | 401 | `loadSession` |
| `POST /auth/logout` (`logout`) | anyone | — | `{message, redirect_url}`; invalidates the session, deletes the cookie | none | `logout` |

### 1.2 Events — `backend/events.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `POST /events` (`create_event_draft`) | Organiser | `EventCreate`: `title` (required, non-blank), `purpose`, `description`, `proposed_start` (datetime), `expected_attendance` (int), `venue_requirements`, `accessibility_needs`, `equipment_requirements`, `registration_needs` | 201: all nine fields + `id, organiser_id, status:"Draft", assigned_coordinator_id, created_at` | 403 not Organiser; 400 `Expected attendance must be greater than 0`; 400 `Proposed start time cannot be in the past`; 422 blank title | `createEventDraft` |
| `POST /events/{id}/submit` (`submit_event`) | Organiser who owns it | — | `{id, status, assigned_coordinator_id, submitted_at}` | 403 not Organiser / not owner; 404; 400 status not Draft/Returned; 400 title missing; 400 attendance / past start (re-checked) | `submitEvent` |
| `POST /events/{id}/amend` (`amend_event`) | Organiser who owns it | `EventAmend`: any of the nine fields; only the fields sent are applied | `{message, id, …nine fields, organiser_id, status, assigned_coordinator_id, amended_fields, changed_fields}` | 403 not Organiser / not owner; 404; 400 Approved/Rejected ("raise a change request"); 400 any status except Submitted/Under Review/Returned; 400 empty body; 400 attendance/past start (only for the fields sent); 422 blank title | `amendEvent` |
| `POST /events/{id}/review` (`start_review`) | assigned Coordinator (via `change_status`) | optional `{reason}` | `{id, status:"Under Review", assigned_coordinator_id}` | 404; 400 invalid transition; 403 not Coordinator / not assigned | `startReview` |
| `POST /events/{id}/approve` (`approve_event`) | assigned Coordinator | optional `{reason}` | `{id, status:"Approved", assigned_coordinator_id}` | 403 not Coordinator; 404; 400 invalid transition; 403 not assigned | `handleApprove` |
| `POST /events/{id}/reject` (`reject_event`) | assigned Coordinator | `{reason}`, required, non-blank | `{id, status:"Rejected", assigned_coordinator_id, rejection_reason}` | 403; 404; 400 invalid transition; 422 blank reason | `handleReject` |
| `POST /events/{id}/return` (`return_event_for_clarification`) | assigned Coordinator | `{reason}`, required, non-blank | `{id, status:"Returned", assigned_coordinator_id, clarification_reason}` | 403; 404; 400 invalid transition; 422 blank note | `handleReturn` |
| `GET /events` (`list_my_events`) | Organiser (their own), Coordinator (assigned to them) | — | list of `{id, title, purpose, description, proposed_start, expected_attendance, status, organiser_id, assigned_coordinator_id, created_at, submitted_at, clarification_note}`. **No** venue, accessibility, equipment or registration fields. | 403 any other role | `fetchEvents` (Organiser only) |
| `GET /events/{id}` (`get_visible_event`) | Organiser (own, else 404); Coordinator & Lead (any non-Draft, Draft → 404); Attendee (Confirmed only, else 404) | — | Organiser/Coordinator/Lead: the nine fields + `id, status, organiser_id, assigned_coordinator_id, organiser, assigned_coordinator, clarification_note`. Attendee: `{name, description, date, time, venue_name}` | 404 (not found, or hidden); 403 other roles | `openEventDetail`, `loadAmendDetail` |
| `PATCH /events/{id}` (`update_event_routine_info`) | the owning Organiser, or the assigned Coordinator | `EventRoutineUpdate` (extra fields refused): `title, purpose, description, accessibility_needs, registration_needs` | `event_to_dict` (no names) + `message, changed_fields` | 404; 403 not entitled; 422 significant field sent (`proposed_start, expected_attendance, venue_requirements, equipment_requirements`); 400 status not Approved/Planning/Confirmed; 400 empty body; 422 blank title / unknown field | `saveRoutineEdit` |
| `GET /events/{id}/bookings` (`get_event_bookings`) | Coordinator (assigned only), Lead, Venue Staff | — | list of `{id, booking_id, event_id, venue_id, venue_name, booking_start, booking_end, status, rejection_reason, created_at, hold_expires_at, warning_minutes}` | 403 role / not assigned; 404 | `openBookings`, `submitBooking` |

### 1.3 Coordinator lists — `backend/coordinator.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `GET /coordinator/events/data` (`assigned_requests_data`) | Coordinator | — | events assigned to the caller: `{id, title, organiser, proposed_start, expected_attendance, status, awaiting_first_review}`. Submitted first (longest wait first), then by proposed start. | 403 | `fetchAssignedEvents` |
| `GET /coordinator/events/all-data` (`all_non_draft_events_data`) | Coordinator | — | every non-Draft event: `{id, title, organiser, proposed_start, expected_attendance, status, assigned_coordinator, is_assigned_to_me}` | 403 | `fetchAllCoordinatorEvents` |
| `GET /coordinator/events` (`assigned_requests_page`) | Coordinator | — | HTML page (**deprecated**, SCRUM-98) | 403 | not used (by design) |

### 1.4 Lead — `backend/lead.py`, `backend/assignment.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `GET /lead/events` (`lead_events`) | Lead | — | every non-Draft event: `{id, title, organiser, proposed_start, expected_attendance, status, assigned_coordinator_id, assigned_coordinator, submitted_at}` | 403 | `fetchLeadData` |
| `GET /lead/coordinators` (`active_coordinators`) | Lead | — | `[{id, username}]` where role is coordinator and `is_active_coordinator` | 403 | `fetchLeadData` |
| `POST /events/{id}/assign` (`initial_assignment`) | Lead | `{coordinator_id}` | the `event_row` + `previous_coordinator_id, reassigned_by` | 403; 404; 400 status not Submitted/Under Review/Returned; 400 not an active Coordinator; 400 already assigned ("use reassignment") | `saveAssignment` |
| `POST /events/{id}/reassign` (`reassignment`) | Lead | `{coordinator_id}` | same as assign | as assign; 400 unassigned ("use initial assignment"); 400 same Coordinator | `saveAssignment` |

### 1.5 Venues and bookings — `backend/venues.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `POST /venues` (`create_venue`) | Venue Staff | `VenueCreate`: `name`, `location` (non-blank), `capacity` (>0), `facilities[]`, `accessibility`, `supported_layouts[]`, `operating_hours`, `setup_time_mins` (≥0, default 30), `turnaround_time_mins` (≥0, default 45) | 201 `{message, venue}` | 403; 409 duplicate name (case-insensitive); 422 | `createVenue` |
| `GET /venues` (`list_venues`) | **any signed-in role** | — | active venues (`venue_to_dict`), by name | — | `fetchVenueWorkspace`, `openBookings` |
| `GET /venues/search` (`search_venues`) | Coordinator, Lead, Venue Staff | query: `start_time`, `end_time` (both or neither), `min_capacity`, `location`, `accessibility`, `layout`, `facilities` (comma list, all required), `name`, `available_only` | active venues + `is_available`, `conflict_reason` | 403; 400 only one of start/end; 400 end ≤ start | `searchVenues` (does not send `name`) |
| `GET /venues/{id}` (`get_venue`) | **any signed-in role** | — | `venue_to_dict` (active venues only) | 404 (also for retired) | `openVenueEdit` |
| `PATCH /venues/{id}` (`update_venue`) | Venue Staff | `VenueUpdate`: any venue field (extra fields such as `is_active` refused; required fields can't be null) | `{message, venue, updated_fields, flagged_bookings[]}` | 403; 422 empty body / null / unknown field; 404; 409 duplicate name | `updateVenue` |
| `POST /venues/{id}/retire` (`retire_venue`) | Venue Staff | — | `{message, venue, affected_bookings[]}` (`booking_to_dict`) | 403; 404; 409 already retired | `confirmRetireVenue` |
| `POST /venues/{id}/bookings` (`submit_venue_booking_request`) | Coordinator (assigned event only), Lead | `VenueBookingRequest`: `event_id`, `booking_start`, `booking_end`, optional `venue_requirements` | 201 `{message, booking}` (`booking_detail_dict`), status `Pending` | 403 role / not assigned; 404 venue / event; 400 venue retired; 409 clash (with reason); 422 end ≤ start or start in the past | `submitBooking` (never sends `venue_requirements`) |
| `GET /venues/{id}/bookings` (`list_venue_bookings`) | Coordinator, Lead, Venue Staff | query `status` (optional exact match on the **stored** status) | list of `booking_detail_dict` | 403; 404 | `fetchVenueWorkspace` |
| `GET /venues/bookings/{id}` (`get_booking_for_review`) | Venue Staff | — | `booking_review_dict`: `booking_detail_dict` + `venue`, `decision {decision, decided_by, decided_at, reason}` | 403; 404 | `openStaffBooking` |
| `POST /venues/bookings/{id}/approve` (`approve_booking`) | Venue Staff | — | `{message, booking}` (review dict), status `Confirmed` | 403; 404; 409 expired hold; 409 already decided; 409 `Cannot approve: <clash>` | `decideStaffBooking('approve')` |
| `POST /venues/bookings/{id}/reject` (`reject_booking`) | Venue Staff | `{reason}`, required, non-blank | `{message, booking}`, status `Rejected` | 403; 404; 409 expired hold; 409 already decided; 422 blank reason | `decideStaffBooking('reject')` |

`booking_detail_dict` fields: `booking_id, id, venue_id, event_id, event_title, requester_id, requester_name, booking_start, booking_end, status, rejection_reason, created_at, conflict_flag {conflicting_booking_id, reason, flagged_at} | null, conflict_flag_record {…, state: Active|Resolved} | null, hold_expires_at, warning_minutes`.

### 1.6 Holds — `backend/venue_holds.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `PUT /venues/bookings/{id}/hold` (`set_hold_expiry`) | Venue Staff | `{expires_at}` (datetime) | `{message, booking}` (`booking_detail_dict`, no venue or decision) | 403; 404; 409 expired / not Pending; 422 not in the future; 422 later than booking start | `setStaffHold` |
| `PUT /venues/bookings/{id}/hold/warning` (`set_hold_warning`) | the event's assigned Coordinator only (Lead refused) | `{warning_minutes}` (int ≥ 0) | `{message, booking}` | 403 role / not assigned; 404; 409 expired / not Pending; 422 negative | `updateHoldWarning` |

### 1.7 Calendar — `backend/venue_calendar.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `GET /venues/{id}/calendar` (`venue_calendar`) | Coordinator, Lead, Venue Staff (`require_venue_searcher`) | query `from`, `to` (YYYY-MM-DD, inclusive, ≤ 31 days) | `{venue_id, name, operating_hours, setup_time_mins, turnaround_time_mins, from, to, unavailable_periods: [], days: [{date, operating_hours, busy: [{event_id, status, booked_start, booked_end, occupied_start, occupied_end}]}]}` | 403; 422 `to` before `from`; 422 more than 31 days; 404 venue missing or retired | **No UI** |

### 1.8 Suitability — `backend/suitability.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `GET /events/{id}/venue-suitability` (`venue_suitability`) | Coordinator (assigned only), Lead | query `facilities` (comma list; omitted = "not stated", empty = none required) | `{event_id, title, expected_attendance, required_facilities, venues: [{id, name, capacity, facilities, suitability, checks: [{check, result, detail}]}]}` | 403 role / not assigned; 404 | `checkSuitability` |

### 1.9 Equipment — `backend/equipment.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `POST /events/{id}/equipment-requests` (`record_equipment_requirement`) | Coordinator (assigned only), Lead | `{equipment_type` (non-blank), `quantity` (>0), `technical_requirements?}` | 201 `{message, equipment_request}` | 403 role / not assigned; 404; 409 type already recorded (case-insensitive); 422 | Request details, "Equipment" section ("Add equipment"), assigned Coordinator or Lead |
| `GET /events/{id}/equipment-requests` (`list_event_equipment_requirements`) | Coordinator (**any** event), Lead, Tech Support | — | `{event_id, event_title, event_proposed_start, equipment_requests: [{id, event_id, event_title, event_proposed_start, equipment_type, quantity, technical_requirements, requested_by, created_at}]}` | 403; 404 | Request details, "Equipment" section (Coordinator, Lead) |
| `GET /equipment-requests` (`list_all_equipment_requirements`) | Coordinator (own events only), Lead, Tech Support | — | `{equipment_requests: [...]}`, soonest event first, undated last | 403 | Technical Support "Equipment worklist" |

No route amends or removes an equipment request (module docstring: SCRUM-40). Logged in `BACKEND_ISSUES.md` #2.

### 1.9a Attendee registration — `backend/registrations.py` (SCRUM-45, 46, 47, 48, 52, 65)

| Endpoint | Who | Notes |
| :--- | :--- | :--- |
| `GET /registrations/events` | Attendee | Open events (setting on and Confirmed), whitelisted fields only. `places_left` is null when the number of places is unknown. |
| `POST /registrations/events/{id}` | Attendee | Register. 404 if not open, 409 if full, unknown capacity or duplicate. |
| `POST /registrations/events/{id}/withdraw` | Attendee | Withdraw. 409 on a Cancelled or Completed event. |
| `GET /registrations/mine` | Attendee | My registrations, newest first. |
| `GET /events/{id}/registrations` | Organiser (own), assigned Coordinator, Lead | Who registered. Another Organiser and Attendees get 404. |
| `GET` / `PUT /events/{id}/registration-settings` | Organiser (own), assigned Coordinator; Lead can only read | Switch registration on or off. Another Organiser and Attendees get 404. |

### 1.10 Notifications — `backend/notifications.py`

| Method & path | Roles | Request | Response | Errors | Used by UI |
|---|---|---|---|---|---|
| `GET /notifications` (`list_my_notifications`) | any signed-in role | — | own notifications, newest first, filtered by `can_see_notification`: `{id, event_id, booking_id, kind, message, created_at, read, read_at}` | — | `fetchNotifications` (polled every 15 s by `refreshWorkspace`) |
| `POST /notifications/{id}/read` (`mark_notification_read`) | the recipient | — | the notification | 404 not yours / missing | `markNotificationRead` |

Notification kinds and recipients:

| Kind | Recipient | Source |
|---|---|---|
| `submitted`, `returned` (with note), `approved`, `rejected` (with reason) | the Organiser | `notify_organiser_of_status` |
| `coordinator_assigned` / `assigned_to_you` | the Organiser / the Coordinator, on first assignment | `notify_first_assignment` |
| `reassigned_to_you`, `coordinator_changed`, `reassigned_away` (no event link) | the new Coordinator, the Organiser, the former Coordinator | `assignment.assign_event`, `notify_former_coordinator` |
| `booking_submitted` | every Venue Staff user | `notify_booking` |
| `booking_approved`, `booking_rejected` (with reason) | the booking's requester | `notify_booking` |
| `hold_warning`, `hold_expired`, `hold_expiry_set` | the event's assigned Coordinator (none if unassigned) | `venue_holds._tell_coordinator` |

### 1.11 Other

| Method & path | Roles | Response | Source |
|---|---|---|---|
| `GET /` | anyone | `{status, service, docs_url}` | `backend/main.py` `root` |

---

## 2. Event request statuses

Vocabulary: `Draft, Submitted, Under Review, Approved, Rejected, Returned, Planning,
Confirmed, Completed, Cancelled` (`backend/status.py`).

| From | To | Who | Endpoint | Source |
|---|---|---|---|---|
| (new) | Draft | Organiser | `POST /events` | `events.create_event_draft` |
| Draft | Submitted | owning Organiser | `POST /events/{id}/submit` | `events.submit_event`; `status_rules.TRANSITIONS` |
| Returned | Submitted | owning Organiser | `POST /events/{id}/submit` | same |
| Submitted | Under Review | assigned Coordinator | `POST /events/{id}/review` | `status_rules.TRANSITIONS`, `check_transition` |
| Submitted / Under Review | Approved | assigned Coordinator | `POST /events/{id}/approve` | same |
| Submitted / Under Review | Rejected (reason required) | assigned Coordinator | `POST /events/{id}/reject` | same |
| Submitted / Under Review | Returned (note required) | assigned Coordinator | `POST /events/{id}/return` | same |

Changes that keep the status:

- **Amend:** Organiser, while Submitted, Under Review or Returned (`events.amend_event`).
- **Routine update:** Organiser or assigned Coordinator, while Approved, Planning or Confirmed (`events.update_event_routine_info`, `PLANNING_STATUSES`).
- **Assign / reassign:** Lead, while Submitted, Under Review or Returned (`lead.apply_assignment`).

Every one of these writes an `EventStatusHistory` row.

- **Planning, Confirmed, Completed, Cancelled:** nothing moves an event into them. The `status_rules` docstring says those transitions arrive in later sprints.
- **⚠ Uncertain:** `submit_event` sets the status directly and does not call `change_status`, so its role and ownership checks are its own. They match `TRANSITIONS`.
- **⚠ Uncertain:** `/approve`, `/reject` and `/return` return 403 for a non-Coordinator before looking the event up. `/review` looks the event up first, so a non-Coordinator gets 404 for a missing event.
- **⚠ Uncertain:** `events.py` checks roles with `current_user.role.lower()` without trimming. A role with surrounding spaces would fail there but pass `auth.py`.

## 3. Venue booking statuses

| From | To | How | Source |
|---|---|---|---|
| (new) | Pending (a tentative hold) | Coordinator/Lead request, if no clash | `venues.submit_venue_booking_request` |
| Pending | Confirmed | Venue Staff approve; the clash is re-checked | `venues._decide_booking` |
| Pending | Rejected (reason required) | Venue Staff reject; never blocked by a clash | `venues._decide_booking` |
| Pending | Expired | the hold's expiry has passed: stored by `run_hold_checks`, and shown as Expired by `hold_fields` even before it is stored | `venue_holds.check_hold`; `booking_rules.hold_fields`, `is_expired_hold` |

- **Final states:** Confirmed, Rejected and Expired. A decided booking returns 409, and so does an expired hold (`_decide_booking`, `_refuse_unless_live_hold`).
- **Confirmed bookings never expire** (`booking_rules.is_expired_hold`).
- **Retiring a venue** changes no booking status. It only lists the bookings still holding the venue (`venues.retire_venue`).
- **Changing setup or turnaround** never changes a status either. It flags the bookings that now clash (`booking_recheck.recheck_venue_bookings`). A flag is shown only while the clash still exists (`active_conflict_flag`).
- **Blocking statuses:** `Confirmed, Approved, Tentative, Pending`, excluding expired holds (`booking_rules.BLOCKING_STATUSES`, `is_blocking`).
- **⚠ Uncertain:** no code writes `Approved` or `Tentative` as a booking status. They look like legacy values.

## 4. Business rules

### 4.1 Booking hold expiry

| Rule | Source |
|---|---|
| A hold is a Pending booking. Its default expiry is the sooner of 48 h after the request and the booking's start. It is stored when the request is made. | `booking_rules.default_expiry`, `DEFAULT_HOLD_HOURS`; `hold_store.store_default_expiry` |
| The stored expiry wins. Older holds without one fall back to the default. | `booking_rules.hold_expires_at` |
| A hold has expired at its expiry time exactly (`now >= expires`). | `booking_rules.is_expired_hold` |
| Venue Staff may set or extend the expiry: it must be in the future and no later than the booking's start. This also resets the warning. | `venue_holds.set_hold_expiry` |
| The default warning lead time is 24 h. Only the assigned Coordinator may change it (≥ 0); a change resets the warning. | `booking_rules.DEFAULT_WARNING_MINUTES`; `venue_holds.set_hold_warning` |
| There is no timer. Warnings and expiry are processed at the start of requests on four routers (§0), each notice is sent once, and they go to the assigned Coordinator only. | `venue_holds.run_hold_checks`, `check_hold`, `_tell_coordinator` |
| An expired hold no longer blocks its venue. | `booking_rules.is_blocking` |

### 4.2 Capacity

| Rule | Source |
|---|---|
| Venue capacity must be > 0 (create and update). | `venues.VenueCreate.check_capacity` (inherited by `VenueUpdate`) |
| Event expected attendance must be > 0 when given (create, submit, amend). | `events.validate_event_business_rules` |
| Search `min_capacity` keeps venues with capacity ≥ the value. | `venues.search_venues` |
| Suitability: capacity ≥ attendance passes; no attendance gives "unknown". | `suitability.capacity_check` |
| **⚠ Uncertain:** a booking request is not checked against the event's attendance, so a venue that's too small can be requested and approved. | `venues.submit_venue_booking_request`, `_decide_booking` (no capacity check) |

### 4.3 Booking clashes and buffers

| Rule | Source |
|---|---|
| A booking occupies `[start − setup, end + turnaround]`. Windows that only touch do not clash. | `booking_rules.occupied_window`, `windows_overlap` |
| A request clashes with any blocking booking at that venue. The earliest one is named in the 409. | `venues.check_venue_availability`; `booking_rules.find_conflicts` |
| A retired venue can't be requested (400), searched, or shown in its calendar (404). | `venues.submit_venue_booking_request`, `search_venues`; `venue_calendar.venue_calendar` |

### 4.4 Time zones

| Rule | Source |
|---|---|
| **Booking times and hold times are stored as naive UTC.** A value with an offset is converted to UTC; a value without one is taken as already UTC. | `booking_rules.to_naive_utc`, `utc_now` |
| Search times are converted the same way. | `venues.search_venues` |
| Message text gives booked periods in **Singapore time (UTC+8)** and hold expiries in **UTC** (labelled "UTC"). | `booking_rules.singapore_time`; `venue_holds._describe`, `check_hold`; `venues.check_venue_availability`; `booking_recheck.clash_reason`; `notifications.notify_booking` |
| JSON times (`booking_start`, `hold_expires_at` …) are naive ISO strings with no zone marker. | `venues.booking_detail_dict`; `booking_rules.hold_fields` |
| The frontend reads booking and hold JSON times as UTC, and sends the `datetime-local` inputs as UTC ISO strings. | `frontend/app.js` `utcTimestamp`, `formatSchedule`, `submitBooking`, `searchVenues`, `setStaffHold` |
| In messages, the frontend converts times marked UTC to local time and shows unmarked times as written. | `frontend/app.js` `formatServerTimes` |
| **⚠ Uncertain — event `proposed_start`:** the frontend sends the `datetime-local` value with no zone and the backend stores it as given. `validate_event_business_rules` compares a naive value with `datetime.utcnow()`, so the backend treats it as UTC, while `formatDate` parses it as the browser's local time. In Singapore, a start up to 8 h in the past can pass the "not in the past" check. | `events.validate_event_business_rules`; `frontend/app.js` `createEventDraft`, `formatDate` |
| **⚠ Uncertain:** the calendar's `from`/`to` days are UTC days (naive midnight boundaries), not Singapore days. | `venue_calendar.venue_calendar` |

### 4.5 Attendee event visibility

| Rule | Source |
|---|---|
| An Attendee may read one event only when its status is `Confirmed`; anything else is 404. The response is limited to `{name, description, date, time, venue_name}`, where the venue is the earliest Confirmed booking. | `events.get_visible_event` |
| `GET /events` is still 403 for Attendees. Since SCRUM-45 they list open events with `GET /registrations/events` (§1.9a). | `events.list_my_events`, `registrations.list_open_events` |
| Nothing moves an event to `Confirmed` (§2), so in practice an Attendee can see no event. | `status_rules.TRANSITIONS` |
| Since SCRUM-52 an Attendee receives notices only about events they hold a place on. | `notifications.can_see_event` |

### 4.6 Other visibility rules

| Rule | Source |
|---|---|
| Organisers see only their own events; another Organiser's event returns 404, not 403. | `events.get_visible_event` |
| Coordinators and Leads see every non-Draft event. A Coordinator may act only on events assigned to them. | `events.get_visible_event`; `coordinator.all_non_draft_events_data`; `status_rules.check_transition` |
| Organisers can't search venues, so they can't see other events' booking times. | `venues.require_venue_searcher` |
| **⚠ Uncertain:** `GET /venues` and `GET /venues/{id}` accept **any** signed-in role, including Organiser and Attendee. They return venue details, not bookings. | `venues.list_venues`, `get_venue` |
| A notification is hidden once its recipient can no longer see the event, for example after a reassignment. | `notifications.can_see_notification` |

---

## 5. Roles: screens, data and allowed actions

"Backend allows" is what the API permits. "UI" is what the frontend shows now
(`frontend/src/rbac.js` `NAVIGATION`, `frontend/index.html`).

### Organiser — `/events`, `/events/new`

| Screen | Data (endpoint) | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| My event requests | `GET /events` | Open request (`GET /events/{id}`); Amend (Submitted, Under Review, Returned → `POST …/amend`, after loading `GET /events/{id}`); Submit for review (Draft, Returned) | — |
| Create request | — | Save draft (`POST /events`) | — |
| Event detail dialog | `GET /events/{id}` | Edit details (routine fields, while Approved/Planning/Confirmed → `PATCH /events/{id}`) | — |
| Notifications | `GET /notifications` | Mark as read | — |

### Coordinator — `/coordinator/events`, `/coordinator/all-events`

| Screen | Data | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| Assigned requests | `GET /coordinator/events/data` | Start review (Submitted); Approve; Request clarification; Reject (Submitted/Under Review); Open request; Venue bookings | Record and list equipment (`POST`/`GET /events/{id}/equipment-requests`); own equipment worklist (`GET /equipment-requests`) |
| All events (read-only) | `GET /coordinator/events/all-data` | Open request | — |
| Event detail dialog | `GET /events/{id}` | The same decisions when assigned; Edit details while Approved+ | — |
| Venue bookings dialog | `GET /venues`, `GET /events/{id}/bookings` | Search venues; Check suitability; Request a venue; Set hold warning (own live Pending holds) | Venue calendar (`GET /venues/{id}/calendar`); `venue_requirements` on a booking request; search by `name` |

### Event Coordinator Lead — `/lead/events`

| Screen | Data | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| Assignment oversight | `GET /lead/events`, `GET /lead/coordinators` | Assign / Reassign (only for Submitted/Under Review/Returned); Open request; Venue bookings | Record and list equipment for any event; equipment worklist for every event |
| Venue bookings dialog | as Coordinator | Search; Check suitability; Request a venue (any event) | Venue calendar. Setting the hold warning is Coordinator-only (correctly hidden). |

### Venue Staff — `/venue`

| Screen | Data | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| Venue operations | `GET /venues`, then `GET /venues/{id}/bookings` per venue | Add venue; Edit venue (`GET`/`PATCH /venues/{id}`); Retire venue; filter bookings by status; Refresh | Venue calendar; `GET /events/{id}/bookings` |
| Booking review dialog | `GET /venues/bookings/{id}` | Approve; Reject (reason); Set or extend the hold (Pending only) | Decision audit (`decision.decided_by`, `decided_at`) and `conflict_flag_record` are returned but not shown |
| Notifications | `GET /notifications` | Opening a booking notification opens its review | — |

### Technical Support — `/technical`

| Screen | Data | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| Equipment worklist | `GET /equipment-requests` | Search, sort, Refresh (read only) | Per-event list `GET /events/{id}/equipment-requests` (same data). No status or assignment exists (`BACKEND_ISSUES.md` #14). |

### Attendee — `/attendee`

| Screen | Data | Actions in the UI | Backend allows, not in the UI |
|---|---|---|---|
| Event directory: open events and my registrations (SCRUM-45, 46, 47) | Register, Withdraw | none | The routes in §1.9a. |

---

## 6. Mismatches and missing UI

### 6.1 Backend features with no UI

| # | Feature | Endpoint | Roles | Notes |
|---|---|---|---|---|
| G1 | Equipment worklist | `GET /equipment-requests` | Tech Support, Lead, Coordinator | **Done** for Technical Support (Equipment worklist). Leads and Coordinators see per-event lines in the request details instead. |
| G2 | Record and list an event's equipment | `POST`/`GET /events/{id}/equipment-requests` | Coordinator (assigned), Lead; reading also Tech Support | **Done:** request details, "Equipment" section. No amend or remove route (`BACKEND_ISSUES.md` #2). |
| G3 | Venue availability calendar | `GET /venues/{id}/calendar` | Coordinator, Lead, Venue Staff | `unavailable_periods` is always `[]`. |
| G4 | Booking decision audit | `decision` in `GET /venues/bookings/{id}` | Venue Staff | Who decided, and when. |
| G5 | Conflict flag history | `conflict_flag_record` | Coordinator, Lead, Venue Staff | Only the live `conflict_flag` is shown. |
| G6 | Search by venue name | `name` query on `/venues/search` | Coordinator, Lead, Venue Staff | |
| G7 | Venue requirements on a booking request | `venue_requirements` in `POST /venues/{id}/bookings` | Coordinator, Lead | It **overwrites** the event's `venue_requirements` (`venues.submit_venue_booking_request`). Not adding it to the UI may be deliberate. |
| G8 | Event bookings for Venue Staff | `GET /events/{id}/bookings` | Venue Staff | Covered in practice by the per-venue lists. |

### 6.2 Frontend that disagrees with or departs from the backend

| # | Where | Mismatch | Source |
|---|---|---|---|
| M1 | Event detail dialog | Lists every key the API returns, including raw `organiser_id` and `assigned_coordinator_id`, with generic labels. | `frontend/index.html` event detail; `app.js` `eventFieldLabel` |
| M2 | Status badges | `statusClass` styles only Submitted, Under Review, Approved, Rejected and Returned. Draft, Planning, Confirmed, Completed and Cancelled get the neutral style. | `app.js` `statusClass` |
| M3 | Notifications | Clicking a notification only navigates for Venue Staff (to the booking). For every other role it only marks it read, even when it has an `event_id`. | `app.js` `openNotification` |
| M4 | Lead / Coordinator booking dialog | The UI offers Venue bookings for events in any status (Lead: any non-Draft; Coordinator: any assigned). The backend doesn't check the event's status either. **⚠ Uncertain** whether booking a venue for a Rejected or Returned event is intended. | `app.js` `canBookEvent`; `venues.submit_venue_booking_request` |
| M5 | Venue Staff role spelling | The frontend and `auth.py` accept both `venue staff` and `venue_staff`. Every venue guard, booking viewer and the `booking_submitted` notification accept only `venue_staff`, so a `venue staff` account would get the workspace and then 403s. Seed data uses `venue_staff`. **⚠ Uncertain** whether such accounts exist; a backend fix if they do. | `auth.SUPPORTED_APPLICATION_ROLES`; `venues.require_venue_staff`, `require_venue_searcher`, `require_booking_viewer`; `events.get_event_bookings`; `notifications.notify_booking`; `frontend/src/rbac.js` `NORMALIZED_ROLES` |
| M6 | Technical Support role spelling | Both spellings are accepted by `auth.py` and `equipment.py`; consistent. | `equipment.TECHNICAL_SUPPORT` |
| M7 | Event proposed start | Sent with no zone and compared by the backend as UTC; shown by the UI as local time (§4.4). | `events.validate_event_business_rules`; `app.js` `formatDate` |
| M8 | Venue Staff summary | "Active venues" is labelled "Available to book" but is just the active-venue count, not availability. | `index.html` venue summary strip |
| M9 | Booking filters | Filters match the status shown (`Expired` comes from `hold_fields`), which agrees with the backend. `GET /venues/{id}/bookings?status=` filters on the stored value; the UI filters on the client, so this is consistent. | `app.js` `filteredVenueBookings` |
| M10 | Lead "Active coordinators" | Counts `GET /lead/coordinators`, which lists only active Coordinators. Consistent. | `lead.active_coordinators` |

### 6.3 Backend gaps (need a backend change)

Logged in `BACKEND_ISSUES.md`:

1. No route lists events for Attendees, and nothing moves an event to Confirmed (§4.5).
2. Equipment requests can't be amended or removed (§1.9).

Seen here, not yet logged (each needs a team decision first):

- No capacity check on booking requests or approvals (§4.2).
- The time zone of event `proposed_start` is ambiguous (§4.4).
- Venue role spelling (M5).
- No event-status check on venue booking requests (M4).
