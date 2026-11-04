# Backend issues

Frontend work that is blocked on a backend change. The frontend does not work
around these; each entry has the evidence.

## 1. Attendees have no way to list events

- **Screen:** Attendee workspace (`/attendee`, "Event directory"), currently a placeholder.
- **Evidence:**
  - `backend/events.py` `GET /events` (`list_my_events`) returns 403 for every role
    except Organiser and Coordinator.
  - `backend/events.py` `GET /events/{event_id}` does serve Attendees, but only for a
    Confirmed event whose id they already know.
  - No other route returns events to an Attendee.
- **Needed:** a list route for Attendees (for example, Confirmed events with the same
  fields `GET /events/{id}` returns them: name, description, date, time, venue_name).
- **Frontend until then:** keep the placeholder page.

## 2. Equipment requests cannot be amended

- **Screen:** equipment requirements for an event (Coordinator / Lead).
- **Evidence:**
  - `backend/equipment.py` `POST /events/{event_id}/equipment-requests` returns 409 for a
    duplicate equipment type with the message "Amend that entry instead of adding a
    second one."
  - `backend/equipment.py` has no PATCH, PUT or DELETE route; its module docstring says
    reviewing and changing a request is SCRUM-40.
- **Needed:** an amend (and probably remove) route for an equipment request.
- **Frontend until then:** show the 409 message as returned; offer no Edit or Remove control.

## 3. SCRUM-95 booking time fix not applied on the shared database

- **Screen:** Venue bookings (Coordinator, Lead), Booking requests (Venue Staff). See `UI_AUDIT.md` A1.
- **Evidence:**
  - `python -m backend.migrate_scrum95 --check --created-before 2026-11-03T02:00` (read-only)
    against the Neon database in `.env` prints "2 booking(s) would move 8 hours earlier".
  - Booking #1 (Annual Gala Dinner, created 2026-10-28) is stored as 2026-11-07 16:45–21:45,
    the Singapore time typed before SCRUM-95. The UI reads stored booking times as UTC
    (`booking_rules.to_naive_utc`, D7), so it shows Nov 8, 00:45–05:45.
  - **⚠ Uncertain:** the real SCRUM-95 deploy time. The cutoff above is the example in
    `docs/SCRUM-95-UTC-MIGRATION.md`.
- **Needed:** confirm the deploy time, back up the database, and run the migration with
  `--apply` (per `docs/SCRUM-95-UTC-MIGRATION.md`). If the data can be reset instead,
  reset it.
- **Frontend until then:** nothing. The UI is correct for UTC data.

## 4. Booking requests and approvals don't check venue capacity

- **Screen:** Venue bookings, Booking review. See `UI_AUDIT.md` A2.
- **Evidence:**
  - Event #3 (expected attendance 180) holds booking #2 at venue Test1 (capacity 150).
  - `GET /events/3/venue-suitability` returns `unsuitable` ("capacity 150 is below expected
    attendance 180"), but `venues.submit_venue_booking_request` and `venues._decide_booking`
    have no capacity check.
- **Needed:** decide the rule (refuse, or warn and record), then enforce it in the request
  and/or approval.

## 5. Venue bookings accepted for events in any status

- **Screen:** Venue bookings. See `UI_AUDIT.md` A3.
- **Evidence:**
  - Booking #2 was created 2026-11-01 for event #3, which was rejected 2026-10-27 (its
    `rejected` notification).
  - `venues.submit_venue_booking_request` checks role, assignment, venue and clashes, but
    not `event.status`.
- **Needed:** decide which event statuses may request a venue (for example only Approved,
  or Approved/Planning/Confirmed), and refuse the rest.
- **Frontend until then:** "Venue bookings" stays visible for every status, as the backend
  allows it. Hiding it in the UI alone would be a workaround.

## 6. Venue Staff booking review has no event attendance or status

- **Screen:** Booking review dialog. See `UI_AUDIT.md` A4.
- **Evidence:** `venues.booking_review_dict` returns `booking_detail_dict` (event id and
  title only) plus the venue and the decision. No expected attendance, event status or
  proposed date.
- **Needed:** include `expected_attendance` and the event `status` (and possibly
  `proposed_start`) in the review response, so Venue Staff can check capacity.

## 7. Event detail returns an old clarification note

- **Screen:** Event detail dialog. See `UI_AUDIT.md` A5.
- **Evidence:**
  - `GET /events/1` (status Approved) returns `clarification_note: "TEST"`.
  - `events.get_visible_event` picks the latest `Returned` history row with no status check.
  - `events.list_my_events` uses `latest_clarification_note`, which returns `None` unless the
    event is Returned.
- **Needed:** use `latest_clarification_note` in `get_visible_event` too, or rename the field
  if the history is wanted.

## 8. No rule linking a booking's dates to the event's date (needs a decision)

- **Screen:** Venue bookings. See `UI_AUDIT.md` A6.
- **Evidence:** event #1 is proposed for 2027-10-15; its bookings are in November 2026.
  No backend code compares `booking_start`/`booking_end` with `event.proposed_start`.
- **Needed:** a product decision on whether bookings must cover, or be near, the event's
  proposed date.

## 9. No way to mark all notifications as read

- **Screen:** Notification panel (top bar), every role.
- **Evidence:** `backend/notifications.py` has only `GET /notifications` and
  `POST /notifications/{notification_id}/read`, which marks one row. There is no bulk route.
- **Frontend:** no "Mark all as read" button was added. Sending one request per unread
  notification would fake a bulk action and could half-finish if one request fails.
- **Needed:** a `POST /notifications/read-all` (or similar) that sets `read_at` on all of
  the current user's visible unread notifications in one transaction.

## 10. Organisers can't read a rejection reason from the event

- **Screen:** Organiser request list (Closed group) and event detail dialog.
- **Evidence:** `events.reject_event` saves the reason on an `EventStatusHistory` row and
  returns it once as `rejection_reason`. `events.list_my_events` and
  `events.get_visible_event` return no rejection reason (only `clarification_note`).
  The only other copy is the Organiser's `rejected` notification message
  (`notifications.notify_organiser_of_status`: `"... has been rejected. Reason: ..."`).
- **Frontend:** the list shows the reason from that notification when there is one, and says
  "No reason is shown here" when there isn't. It doesn't guess.
- **Needed:** a `rejection_reason` field (latest `Rejected` history row) on `GET /events` and
  `GET /events/{id}`, like `latest_clarification_note`.

## 11. Drafts can't be edited

- **Screen:** Organiser request list (Drafts group) and the create form.
- **Evidence:** `events.amend_event` refuses `Draft` ("A Draft should be completed and submitted
  instead."), and `events.update_event_routine_info` only accepts Approved, Planning and
  Confirmed. No other route updates a Draft.
- **Frontend:** Drafts offer only "Submit for review" and "View details", with a note that a
  draft can be amended after it is submitted. The create form says the same.
- **Needed:** a decision: either let `amend_event` (or a new `PATCH`) update Drafts, or treat a
  Draft as final until submitted, in which case "Save as draft" may not be worth keeping.

## 12. Returned times can't be shown as reliable Singapore Time (blocks the timezone label change)

- **Screen:** every role: event dates, booking periods, hold expiries, created, submitted and
  notification times. Blocks replacing the "Times in SGT" pill with per-value "SGT" labels.
- **Evidence:**
  - **Event `proposed_start` has no defined zone.** `EventCreate`/`EventAmend` accept a naive
    datetime and store it as given. The frontend sends the `datetime-local` value as a Singapore
    wall time and shows it as one (`app.js` `parseServerTime(value, "local")`).
    `events.validate_event_business_rules` compares the same naive value with
    `datetime.utcnow()`, so the backend treats it as UTC. They disagree by 8 hours
    (UI_BACKEND_MAP.md §4.4, M7).
  - **Some stored booking times are not UTC.** Bookings are meant to be naive UTC
    (`booking_rules.to_naive_utc`, D7), and the frontend converts them to Singapore time. On the
    shared database, rows written before SCRUM-95 still hold Singapore wall times (#3:
    `migrate_scrum95 --check` reports 2 bookings), so they show 8 hours late.
  - JSON times carry no zone marker (`venues.booking_detail_dict`, `events.list_my_events`), so
    the frontend can't tell which convention a value follows.
- **Frontend:** unchanged. The "Times in SGT" pill and the existing formatting stay. Labelling
  each value "SGT" would state a zone the data doesn't guarantee.
- **Needed:** decide the zone of `proposed_start` (store UTC like bookings, or validate it as
  Singapore time), apply the SCRUM-95 migration (#3), and ideally return times with an explicit
  offset (`...Z` or `+08:00`).
