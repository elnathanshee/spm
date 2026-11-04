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
