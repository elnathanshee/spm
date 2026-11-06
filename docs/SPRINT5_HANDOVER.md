# Sprint 5 handover (ConnectSphere, 6 Nov 2026)

**All six Week 7 customer changes are on `main`.** Every group merged in the agreed order (A to H), one at a time, before 22:00 SGT. **Merges after 22:00: none.**

Two keys are only partly done: SCRUM-44 and SCRUM-84. No key is left not started.

Acceptance criteria came from the sprint prompt. The Jira connector isn't authorised in the build session, so nothing was compared with the Jira text.

## State of every Jira key

| Key | What | State | PR | Merge commit | TC numbers added |
|---|---|---|---|---|---|
| SCRUM-83 | Multiple venues (change 3) | Merged | PR 50 | `b3a3af1` | TC-8301 to TC-8323 |
| SCRUM-82 | Venue unavailable after booking (change 2) | Merged | PR 58 | `38a2509` | TC-8201 to TC-8238, TC-8240 to TC-8256 |
| SCRUM-100 | Bookings at retired venues | Merged | PR 58 | `38a2509` | TC-10030 to TC-10039 |
| SCRUM-101 | Catalogue refresh | Merged | PR 58 | `38a2509` | TC-10160 to TC-10165 |
| SCRUM-102 | Event lifecycle | Merged | PR 56 | `8e17523` | TC-10201 to TC-10256; TC-10290 to TC-10296 (shared foundation, in PR 50) |
| SCRUM-86 | Safety Officer role and workspace (change 6) | Merged | PR 56 | `8e17523` | TC-8601 to TC-8633 |
| SCRUM-87 | Safety decisions (change 6) | Merged | PR 56 | `8e17523` | TC-8701 to TC-8715 |
| SCRUM-66 | Confirmed and cancelled notices | Merged | PR 56 | `8e17523` | TC-6601 to TC-6606 |
| SCRUM-41 | Equipment catalogue | Merged | PR 54 | `17222e0` | TC-4101 to TC-4108, TC-4121 to TC-4127 |
| SCRUM-42 | Update items, unavailable units | Merged | PR 54 | `17222e0` | TC-4201 to TC-4213, TC-4221 to TC-4230 |
| SCRUM-43 | Availability | Merged | PR 54 | `17222e0` | TC-4301 to TC-4317, TC-4321 to TC-4323 |
| SCRUM-44 | Reservations | **Partly done** (merged) | PR 54 | `17222e0` | TC-4401 to TC-4431 |
| SCRUM-40 | Equipment request status | Merged | PR 54 | `17222e0` | TC-4001 to TC-4008, TC-4021 to TC-4023 |
| SCRUM-65 | Notices, both halves | Merged | PR 54, PR 55 | `17222e0`, `bc04ece` | TC-6501 to TC-6505, TC-6550 to TC-6566 |
| SCRUM-45 | Attendees see open events | Merged | PR 55 | `bc04ece` | TC-4501 to TC-4510, TC-4520 to TC-4525 |
| SCRUM-46 | Register | Merged | PR 55 | `bc04ece` | TC-4601 to TC-4617, TC-4620 to TC-4624 |
| SCRUM-47 | My registrations, withdraw | Merged | PR 55 | `bc04ece` | TC-4701 to TC-4713, TC-4720 to TC-4727 |
| SCRUM-48 | Organiser and Coordinator see registrations | Merged | PR 55 | `bc04ece` | TC-4801 to TC-4808, TC-4820 to TC-4827 |
| SCRUM-52 | Attendees told of changes | Merged | PR 55 | `bc04ece` | TC-5201 to TC-5218, TC-5220 |
| SCRUM-49 | Request a change | Merged | PR 52 | `5db400a` | TC-4901 to TC-4921 |
| SCRUM-50 | Decide a change | Merged | PR 52 | `5db400a` | TC-5001 to TC-5026 |
| SCRUM-29 | Routine and important edits | Merged | PR 52 | `5db400a` | TC-2901 to TC-2917 |
| SCRUM-67 | Coordinator told of amendments | Merged | PR 52 | `5db400a` | TC-6701 to TC-6707 |
| SCRUM-85 | Lead oversight and later reassignment | Merged | PR 53 | `d016e55` | TC-8501 to TC-8532 |
| SCRUM-84 | Role-based access check | **Partly done** (merged) | PR 53 | `d016e55` | TC-8401 to TC-8414 |
| SCRUM-99 | CSV matches the Markdown register | Merged | PR 51 | The last merge on `main` | TC-9903 to TC-9920, TC-9924 to TC-9930 |
| PR 48 follow-ups | Calendar on phones, Singapore days and focus; quantity limit; network message; tab icon (all six) | Merged | PR 51 | The last merge on `main` | Within the SCRUM-99 numbers |

**What's missing from the two partly done keys:**
- **SCRUM-44:** "can't over-reserve" has never run as a real race. TC-4404 is sequential, and TC-4405 only checks the row lock is requested. SQLite ignores that lock, and there's no Postgres here to test it on.
- **SCRUM-84:** you need to compare its criteria with Jira, because they came from the PR 18 description. Also, an account with the role `venue staff` is refused on Venue Staff routes (first defect below).

**Three old register rows were corrected**, because their tests or behaviour changed tonight: TC-9934, TC-10113 and TC-10151. TC-9992 was corrected in PR 54. TC-9934, TC-9992 and TC-10113 now say Sprint 5, so Sprint 4's count falls from 246 to 243.

**Two PRs merged outside the plan,** both by `thethangster`:
- **PR 57** (`8352de6`, merged by you at 21:15): test password hashing uses 4 rounds, which cut the backend CI job from about 14 minutes to about a minute.
- **PR 59** (`7a8c6b5`, merged by them at 21:47): sequence diagram 04 and a register recount. PR 51 replaces that recount's numbers.

## Test results

Run in CI on PR 51's cleaned-up head `e86cb76`, on 6 Nov 2026, using in-memory SQLite:

| Suite | Result |
|---|---|
| Backend | 1389 passed, 1 skipped (TC-6442, which always skips in CI) |
| Backend coverage | 96% with branches |
| Frontend | 461 of 461 |
| Register | 1195 rows, 572 of them Sprint 5; the Markdown and CSV hold the same rows in the same order |

Locally on Windows: backend 1390 passed, and frontend 460 of 461. TC-10022 fails locally only because of CRLF line endings.

The last commit on PR 51 adds this file and small C4 and register text. CI's result on that head is in the PR's checks.

## What production still needs

- **A `safety_officer` account in Neon (your decision).** The role works locally, through the seed account `safety1`, but production has no such account. Until it does, nobody in production can approve a safety check, so **no event can reach Confirmed, and registration can't open.**
- **Ten new tables** are created by `create_all` when the deployed app next starts, with no action needed: `equipment_request_updates`, `arrangement_review_flags`, `venue_unavailability`, `safety_checks`, `equipment_items`, `equipment_item_unavailability`, `equipment_reservations`, `event_registration_settings`, `event_registrations`, `event_change_requests`. No column was added to an existing table. `backend/models.py` has no removed lines since v1.0.0.
- **The deployed site was not checked.** Neon was never touched, and nothing was run against production.
- **Other account types in Neon were not checked,** such as Technical Support and Attendee. The new screens need them too.
- **C4 PNGs:** the text is updated, and you said you'd re-render them.
- **Version numbers:** `backend/main.py` and `frontend/package.json` still say 1.0.0. The `v1.1.0` tag and release aren't done yet.

## Known defects

**Most likely to matter**
1. **`venue staff` and `venue_staff` don't match.** `auth.py` and `rbac.js` accept both, but `venues.py`, `events.py` and `notifications.py` accept only the underscore, so a `venue staff` account gets 403 on Venue Staff routes. Seeded accounts use the underscore. Production accounts weren't checked.
2. **Booking outcome notices go to the original requester, not the current Coordinator.** This matters more now that the Lead can reassign later events. After a reassignment, the new Coordinator also isn't told about a pending change request, though they can decide it.
3. **The locks have never run on Postgres.** That covers the last equipment unit, the last registration place and the duplicate checks that rely on row locks. A duplicate catalogue item is refused by code only, with no database constraint.
4. **The final code was not checked in a browser.** Browser tools blocked three agents for about 40 minutes and were then disconnected. UI behaviour rests on frontend tests and earlier partial browser runs. Phone width was checked through CSS text, and focus through stubs, including focus after confirming a withdrawal. Your demo is a good place to check these.
5. **TC-6403 may be flaky.** It failed twice on one merged tree with an `AttributeError` inside SQLAlchemy, then passed 36 repeat runs and every CI run. The cause wasn't found.

**Behaviour gaps**
6. A new equipment line on a Confirmed event is accepted with no new safety check.
7. `POST /equipment-requests/{id}/status` still works on Cancelled and Completed events, and sends the notice. The worklist still shows those lines.
8. Moving an event can leave more equipment units held than exist. This is flagged only in that line's reserve panel, not refused.
9. Cancelling a booking leaves any active review flag on it, so the flag list can name a booking that no longer holds a venue.
10. A pending change request stays Pending when its event is cancelled, until the Coordinator rejects it.
11. An unassigned event already at Approved or later can't be given a Coordinator. Normal flow shouldn't produce one.
12. Editing a venue's own record (name, location, capacity) sends Attendees no notice. A venue unavailability period tells the Coordinator but not registered Attendees.
13. After a second approved change, active flags still show the first change's reason.

**Smaller or older**
14. Older: on create and amend, a start up to 8 hours in the past can pass, because `validate_event_business_rules` compares Singapore time with UTC. Change requests check the Singapore clock themselves.
15. Older: `createVenue` and `updateVenue` return silently if a catalogue refresh is running, so that Save does nothing. Read in the code, not reproduced.
16. Change-request notices show the default "Workspace update" heading.
17. The Safety Officer's list refreshes only on Refresh, reload or a decision.
18. On a phone, the Cancel booking confirm text wraps into short lines in the narrow column.
19. Register Markdown: the TC-3901 block has no table header, and two separator lines have the wrong column count, so those parts may not render as tables.
20. PR 57's `import backend.security` sits above the guard in `conftest.py` that keeps tests off Neon. It's safe today, because that module imports only `passlib`. Move it below the guard.
21. Safari on macOS: returning focus after closing the calendar doesn't work for mouse users, as before.

## Decisions for you

Each of these was built one way so work could continue, and each is easy to change.

| Area | What it does now | The other choice |
|---|---|---|
| Past events | A Confirmed event whose date has passed takes registrations until marked Completed | Refuse and hide them |
| Several venues | The smallest venue capacity sets the registration limit | Add the capacities |
| Routine edits | Attendees are told only when the title or description changes | Tell them on every edit |
| Important changes | An approved change keeps the event's status, even Confirmed, and flags arrangements | Return it to Planning |
| Safety gates | Submit and approve count a Confirmed booking even over an unavailable period; the Coordinator is told and the officer sees the reason | Refuse until it's resolved |
| Safety approval | Approval re-reads the gate and answers 409 if it's no longer met | Refuse new equipment during Safety Review |
| Ended bookings | A period doesn't flag bookings that have already ended | Flag them too |
| Last booking | Cancelling a Confirmed event's last Confirmed booking is allowed, with a warning | Refuse it |
| Rejected events | Can still request a venue (`BACKEND_ISSUES` item 5); Cancelled and Completed can't | Refuse those too |
| Equipment read access | The assigned Coordinator and the Lead can read availability | Technical Support only |
| Limits | Equipment quantity stops at 2147483647, the column's limit; calendar dates run from 2000 to 2999 | A realistic product limit |
| Organiser's PATCH | Doesn't notify the Coordinator; "amended" was read as the amend route | Notify on PATCH too |
| Lead and change requests | A Lead can't read change requests | Give them access |

## How it ran

- **Test-first exceptions.** Group C worked by story batch rather than per criterion: its SCRUM-86 and 87 backend code was written before the tests, held back until they were seen failing. TC-10250 went in with its fix. TC-8632, TC-8633, TC-5025 and TC-5026 check how groups meet and passed first time. TC-10165 was added with its fix, then seen failing with the fix removed. Each group's PR lists its own.
- **Review.** Each group had one independent reviewer. Its findings were fixed or listed in that group's PR. Group C's reviewer ran its route checks before the fixes, and those weren't repeated.
- **Merges.** A safety check refused merges from background agents, so the main session ran each one after checking the head and CI. Every merge used a merge commit and the agreed message, except PR 57 and PR 59, which weren't part of the sprint.
- **CI rule.** Every PR merged with CI green on its exact head, so the "no second run" allowance wasn't needed for a code change. PR 51's last commit is the one to check, as above.
