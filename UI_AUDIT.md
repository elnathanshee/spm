# UI audit

Manual test of the ConnectSphere frontend as every role, against
[UI_BACKEND_MAP.md](UI_BACKEND_MAP.md).

- **Date:** 2026-11-04
- **Branch:** `improve-ui` @ `8738462`
- **App:** `npm run dev` — Vite on `127.0.0.1:5173`, FastAPI on `127.0.0.1:8000`
- **Database:** the shared Neon Postgres from `.env`, not a local SQLite file
- **Browser:** built-in browser pane, time zone Asia/Singapore (UTC+8)
- **Accounts:** `organiser1`, `coord1`, `coord2`, `lead1`, `venue1`, `technical1`, `attendee1`

**Read-only run.** Only screens and `GET` requests were used. Signing in creates a
session row, and nothing else was written. Flows that need a write are listed in §4 as
not tested.

Severity scale:

- **High:** wrong data shown, or a rule that should block an action doesn't.
- **Medium:** a broken or misleading flow, or wasted load.
- **Low:** cosmetic or clarity.

---

## 1. The six reported issues

| # | Report | Real bug? | Where | Detail |
|---|---|---|---|---|
| 1 | Bookings on future dates show as Expired | **No.** It's the backend rule working as designed; the UI explains it poorly (A10). | Backend rule; Frontend display | Expiry is about the **hold**, not the event date. A Pending hold expires 48 h after the request, or at the booking start if sooner, unless Venue Staff extend it (`booking_rules.default_expiry`, `is_expired_hold`). Booking #2 was requested 2026-11-01 14:18 UTC, and its hold lapsed 2026-11-03 14:18 UTC before anyone decided it. Booking #1's hold was set to 2026-10-28 18:20 UTC and lapsed. Both are correctly Expired. |
| 2 | Annual Gala Dinner proposed for Oct 15, 2027, booked for Nov 8, 2026, 12:45–5:45 AM | **Yes, two problems.** (a) The **time** is wrong by 8 h because a data migration was never run (A1). (b) The **date** gap is not checked anywhere (A6). | Backend (data and rules) | (a) Booking #1 was created 2026-10-28, before SCRUM-95, so it holds the Singapore wall time typed (Nov 7, 16:45–21:45). The UI now reads stored booking times as UTC and adds 8 h, which gives Nov 8, 00:45–05:45. `python -m backend.migrate_scrum95 --check --created-before 2026-11-03T02:00` (read-only) reports **"2 booking(s) would move"**, so the fix has not been applied on Neon. (b) Even corrected, a Nov 7, 2026 booking is 11 months before the Oct 15, 2027 event. No code compares a booking window with the event's `proposed_start`. |
| 3 | A 180-guest event booked at a 150-capacity venue | **Yes.** | Backend | Event #3 (180 guests) holds booking #2 at Test1 (capacity 150). The backend's own suitability check says `unsuitable — capacity 150 is below expected attendance 180`, but neither `submit_venue_booking_request` nor `_decide_booking` checks capacity (A2). Venue Staff can't spot it either, because the review response has no attendance (A4). |
| 4 | Empty attendee directory despite an approved event | **No frontend bug.** It's a backend gap. | Backend | Attendees may only see `Confirmed` events (`events.get_visible_event`), and nothing moves an event to Confirmed (`status_rules.TRANSITIONS`). As `attendee1`: `GET /events` → 403, and `GET /events/1` and `/events/2` (both Approved) → 404. There is no list endpoint. The page is a deliberate placeholder (`BACKEND_ISSUES.md` #1). |
| 5 | Actions on approved or rejected requests that may not be allowed | **Partly.** The decision, amend and assign buttons are correctly hidden. **"Venue bookings" is offered on Rejected events, and the backend accepts the request.** | Frontend + Backend | Approve, Clarify and Reject show only for Submitted/Under Review (`canDecideEvent`). Amend shows only for Submitted/Under Review/Returned. Assign shows only for assignable statuses (fixed in `8738462`). "Edit details" on Approved is allowed (`PATCH`, `PLANNING_STATUSES`). But "Venue bookings" shows on Rejected event #3 for `coord1` and `lead1`, and booking #2 was **created 2026-11-01 for event #3, which was rejected 2026-10-27**: the backend accepted it (A3, F3). |
| 6 | Awaiting review and In progress counts of 0 next to a queue of finished requests | **No data bug.** The counts are correct, but the screen is misleading. | Frontend | As `coord2`: "Awaiting review 0", "In progress 0", "0 awaiting first review", and a "Review queue" containing one **Approved** request. "Awaiting" counts Submitted and "In progress" counts Under Review, which is correct. But the queue lists every assigned request, including finished ones, and "In progress" excludes Approved events that are still being planned (F4). |

---

## 2. Issues found

**Fixed since this audit:** F1, F2 and F6, then F4, F5, F7, F8 and F9 (frontend only). Each was checked in the browser
with the affected role. **Still open:** F3, which waits on `BACKEND_ISSUES.md` #5, and the stale clarification
note in F5, which comes from the backend (A5).
The Technical Support worklist was verified against the real, currently empty, `GET /equipment-requests`.

### Frontend

| ID | Severity | Role | Screen | Steps to reproduce | Likely cause |
|---|---|---|---|---|---|
| F1 | Medium | All | Whole app | Sign in as any role and watch the network for 30 s. Every poll (`/notifications` plus the page's list) is sent **twice** every 15 s, and `/auth/me` twice on each load. | `index.html` has `x-init="init()"`, and Alpine 3 also calls a component's `init()` automatically, so `init` runs twice and starts two `setInterval` timers (`app.js` `init`). |
| F2 | Medium | All | Notifications panel | Sign in as `organiser1` at 12:04 SGT and open notifications. The newest (created `2026-11-04T03:55:11` UTC, about 9 minutes earlier) shows **"4 Nov 2026, 3:55 am"**. | `formatNotificationTime` passes the backend's naive UTC timestamp to `new Date()`, which reads it as local time, so times show 8 h early in Singapore. Booking times already go through `utcTimestamp`; notification times don't. |
| F3 | Medium | Coordinator, Lead | Assigned requests; Assignment oversight | As `coord1` or `lead1`, look at event #3 (Rejected). "Venue bookings" is offered and leads to a working booking form. | `canBookEvent` checks role and assignment, not event status. The backend doesn't refuse either (A3), so this needs the backend rule first; the UI shouldn't invent its own. |
| F4 | Low | Coordinator | Assigned requests | As `coord2`: the counts are all 0, while the "Review queue" ("ordered by their current workflow state") shows an Approved request with no actions. | The queue renders all of `/coordinator/events/data`, which includes finished requests. The heading and counts suggest only open review work. |
| F5 | Low | Organiser, Coordinator, Lead | Event detail dialog | Open request #1. It lists "Organiser ID 1" and "Assigned Coordinator ID 3", shows Status twice, and shows "Latest clarification note: TEST" on an **Approved** request. | It renders every key from `GET /events/{id}` (`eventFieldLabel`). The stale note comes from the backend (A5). |
| F6 | Medium | Technical Support | Technical support (placeholder) | Sign in as `technical1`. The page says "There are no technical requirements available right now" without calling any API. `fetch('/equipment-requests')` in the page returns **200 `text/html`** (the app's own index page), not JSON. | The page is a static placeholder (`rbac.PLACEHOLDER_PATHS`), so its "no requirements" claim isn't based on data. `/equipment-requests` is missing from the dev proxy in `vite.config.js`, so it falls through to the app's HTML. The backend endpoint exists (`equipment.list_all_equipment_requirements`). |
| F7 | Low | Attendee | Event directory (placeholder) | Sign in as `attendee1`. The page says "There are no attendee events available right now". | The placeholder never asks the backend. It happens to be true today (A7), but the page can't know that. |
| F8 | Low | Venue Staff | Venue operations → Booking requests | Two future-dated bookings show only "Expired", with no hold expiry or explanation in the list. | The list row doesn't show `hold_expires_at`; it is only in the review dialog. This is how item 1 gets misread. |
| F9 | Low | Venue Staff | Booking review dialog | Open the Expired booking ("View details"). The dialog is titled "Review request" although no decision is possible. | The title is fixed (`index.html` staff booking dialog). |

### Backend (also logged in `BACKEND_ISSUES.md`)

| ID | Severity | Role | Screen | Steps to reproduce | Likely cause |
|---|---|---|---|---|---|
| A1 | High | Coordinator, Lead, Venue Staff | Venue bookings; Booking requests | As `lead1`, open Annual Gala Dinner → Venue bookings. Booking #1 shows Nov 8, 2026, 12:45–5:45 AM; the intended time was Nov 7, 4:45–9:45 PM SGT. | The SCRUM-95 one-off data fix (`backend/migrate_scrum95.py`) hasn't been applied to Neon. `--check` reports 2 bookings (#1, #2) still stored as Singapore wall time. Clash checks against them are 8 h off too. |
| A2 | High | Coordinator, Lead, Venue Staff | Venue bookings; Booking review | Event #3 (180 guests) holds booking #2 at Test1 (capacity 150), although `GET /events/3/venue-suitability` returns `unsuitable`. | No capacity check in `venues.submit_venue_booking_request` or `_decide_booking`. |
| A3 | High | Coordinator, Lead | Venue bookings | Booking #2 was created 2026-11-01 for event #3, which was rejected on 2026-10-27. | `submit_venue_booking_request` never checks the event's status. |
| A4 | Medium | Venue Staff | Booking review dialog | Open booking #2: venue capacity is shown, but the event's expected attendance and status are not. | `venues.booking_review_dict` returns venue details and the event title only. |
| A5 | Low | Organiser, Coordinator, Lead | Event detail dialog | `GET /events/1` (Approved) returns `clarification_note: "TEST"`; `GET /events` returns `null` for the same event. | `get_visible_event` returns the latest Returned note whatever the current status; `list_my_events` (`latest_clarification_note`) only does so while Returned. |
| A6 | Medium (needs decision) | Coordinator, Lead | Venue bookings | Event #1 is proposed for 2027-10-15; its bookings are in Nov 2026. | No rule relates a booking window to the event's `proposed_start`. Whether one is wanted is a product decision. |
| A7 | High | Attendee | Event directory | `attendee1`: `GET /events` → 403; `GET /events/1`, `/events/2` (Approved) → 404. | Attendees see only Confirmed events, nothing reaches Confirmed, and there is no list route (`BACKEND_ISSUES.md` #1). |

---

## 3. Console and network

- **No failed API calls** for any role during normal use, apart from the expected `401` on `/auth/me` before signing in. That `401` is logged twice per load because of F1.
- **No Alpine or JavaScript errors** came from these sessions. The `eventDetail.routine.form.*` errors seen at the start of the console log came from an earlier stubbed test of the close button, not from real use.
- `/equipment-requests` through the dev server returns the index HTML (F6).

## 4. Not tested (they need writes; this run was read-only)

- Create, submit and amend an event request; Coordinator decisions (review, approve, return, reject); Lead assign and reassign.
- Routine "Edit details" save; booking request submit; hold warning save.
- Venue add, edit and retire; Venue Staff approve and reject; set hold expiry.
- Mark notification as read.

These need a disposable database, for example a Neon branch or local SQLite, or permission to write test records to the shared one.
