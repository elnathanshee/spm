# ConnectSphere Test Case Register

This register documents all automated unit and integration test cases across **Sprint 1** and **Sprint 2**, categorized by story and standard **IS212 5-Path Classification** (*Happy Path*, *Negative Path*, *Boundary Path*, *Workflow Variation*, *Cross-Cutting*).

---

## Summary Matrix

| Sprint | Story ID | Story Title | Total Test Cases | Status | Automated Runner |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Sprint 1** | **SCRUM-16** | User Authentication & Session Management | 6 | **Passed** | `pytest backend/tests/test_spine.py` |
| **Sprint 1** | **SCRUM-22 & 27** | Coordinator Assignment & Status Lifecycle | 7 | **Passed** | `pytest backend/tests/test_spine.py` |
| **Sprint 1** | **SCRUM-21** | Event Amendment Before Approval | 15 | **Passed** | `pytest backend/tests/test_scrum21_amend.py` |
| **Sprint 2** | **SCRUM-51** | Submission & Outcome Notifications | 15 | **Passed** | `pytest backend/tests/test_scrum51_notifications.py` |
| **Sprint 2** | **SCRUM-23** | Lead Reassigns Coordinator (Week 7 Change #5) | 18 | **Passed** | `pytest backend/tests/test_scrum23_reassign.py` |
| **Sprint 2** | **SCRUM-63** | Notify on Coordinator Assignment | 8 | **Passed** | `pytest backend/tests/test_scrum63_assignment_notify.py` |
| **Sprint 2** | **SCRUM-30** | Add Venue with Setup/Turnaround Buffers | 10 | **Passed** | `pytest backend/tests/test_scrum30_add_venue.py` |
| **Sprint 2** | **SCRUM-33** | Search & Filter Venues Against Requirements | 13 | **Passed** | `pytest backend/tests/test_scrum33_search_venues.py` |
| **Total** | | | **92 Test Cases** | **100% Passed** | **Zero Regressions** |

---

## Detailed Test Case Log

### 1. SCRUM-16: User Authentication & Session Management (Sprint 1 Spine)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-001** | Happy Path | AC1: Valid credentials | Log in with valid Organiser credentials | Seeded user exists: `org_test` / `pass123` | 1. Open `/login`<br>2. Submit `{"username": "org_test", "password": "pass123"}` | User is logged in, session cookie set, lands on `/events` | HTTP 200: `{"message": "Login successful", "role": "organiser"}` | **Passed** | Sarah |
| **TC-002** | Negative Path | AC3: Wrong password | Log in with correct username and wrong password | Seeded user exists: `coord1_test` | Submit `{"username": "coord1_test", "password": "WrongPass1"}` | Login refused (401 Unauthorized) | HTTP 401: `{"detail": "Invalid username or password"}` | **Passed** | Sarah |
| **TC-003** | Negative Path | AC4: Unknown user | Log in with non-existent username | None | Submit `{"username": "nosuchuser", "password": "Test1234!"}` | Login refused with same 401 generic message | HTTP 401: `{"detail": "Invalid username or password"}` | **Passed** | Sarah |
| **TC-004** | Boundary Path | AC5: Empty fields | Submit login form with empty/whitespace fields | None | Submit `{"username": "", "password": ""}` | Form validation rejects blank fields (422) | HTTP 422 Unprocessable Entity | **Passed** | Sarah |
| **TC-005** | Happy Path | AC2: Log out | Log out ends the active session | User logged in as `coord1_test` | 1. Call `POST /auth/logout`<br>2. Access `/events` | Session invalidated; subsequent access refused (401) | HTTP 200 on logout; HTTP 401 on `/events` | **Passed** | Sarah |
| **TC-006** | Cross-Cutting | AC6: Direct URL protection | Reach protected endpoint directly while logged out | No active session | Call `GET /events` without session cookie | Direct access refused (401 Unauthorized) | HTTP 401: `{"detail": "Not authenticated"}` | **Passed** | Sarah |

---

### 2. SCRUM-22 & SCRUM-27: Coordinator Assignment & Status Lifecycle (Sprint 1 Spine)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-007** | Happy Path | AC1 / AC4: Draft Creation | Create and save an event request draft | Logged in as `org_test` | Call `POST /events` with title, start date, attendance | Event saved as Draft, ID assigned, coordinator null | HTTP 201 Created: `status: "Draft"` | **Passed** | Sarah |
| **TC-008** | Happy Path | AC1–AC3: Assignment | Submit draft and auto-assign coordinator via Round-Robin | Logged in as `org_test`; draft exists | Call `POST /events/{id}/submit` | Status becomes Submitted; active coordinator auto-assigned | HTTP 200: `status: "Submitted"`, `assigned_coordinator_id: 3` | **Passed** | Sarah |
| **TC-009** | Workflow Variation | AC1: Return for Clarification | Coordinator returns event with note | Event Submitted; logged in as coordinator | Call `POST /events/{id}/return` with reason | Status transitions to Returned; note saved | HTTP 200: `status: "Returned"`, reason stored | **Passed** | Sarah |
| **TC-010** | Workflow Variation | AC1: Organiser Resubmission | Organiser resubmits returned event | Event Returned; logged in as organiser | Call `POST /events/{id}/submit` | Status returns to Submitted; retains coordinator | HTTP 200: `status: "Submitted"` | **Passed** | Sarah |
| **TC-011** | Happy Path | AC1: Approval | Coordinator approves submitted event | Event Submitted; logged in as coordinator | Call `POST /events/{id}/approve` | Status transitions to Approved | HTTP 200: `status: "Approved"` | **Passed** | Sarah |
| **TC-012** | Workflow Variation | AC1–AC3: Round-Robin Rotation | Submit multiple events to verify fair coordinator rotation | Logged in as `org_test`; 2 coordinators | Submit event 1, then submit event 2 | Coordinators rotate alternately (Coord 1 != Coord 2) | HTTP 200 (x2): Round-robin balanced | **Passed** | Sarah |
| **TC-013** | Boundary Path | AC1: 0 Active Coordinators | Attempt submission when no active coordinator is available | 0 active coordinators in pool | Call `POST /events/{id}/submit` | Submission refused (400 Bad Request); rolls back safely | HTTP 400: `{"detail": "No active coordinator available"}` | **Passed** | Sarah |

---

### 3. SCRUM-21: Event Amendment Before Approval (Sprint 1 Story - Elnathan)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-101** | Happy Path | AC1: Amend while Submitted | Organiser amends own Submitted request | Logged in as organiser; event Submitted | `POST /events/{id}/amend` with new title & attendance | HTTP 200; changed fields updated, untouched preserved | HTTP 200: Fields updated cleanly | **Passed** | Elnathan |
| **TC-102** | Workflow Variation | AC1: Amend during Review | Organiser amends own Under Review request | Event in Under Review | `POST /events/{id}/amend` with title change | HTTP 200; amendment accepted during review | HTTP 200: Amendment saved | **Passed** | Elnathan |
| **TC-103** | Happy Path | AC2: Status unchanged | Amendment does not revert status to Draft | Event Submitted | `POST /events/{id}/amend` | Status remains Submitted in response & DB | HTTP 200: `status == "Submitted"` | **Passed** | Elnathan |
| **TC-104** | Cross-Cutting | AC2: Audit Log | Amendment writes one status history row | Event Submitted | `POST /events/{id}/amend`, read `EventStatusHistory` | Exactly 1 new row; previous == new == Submitted | Audit record verified | **Passed** | Elnathan |
| **TC-105** | Negative Path | AC3: Approved refused | Amending an Approved request is refused | Event Approved | `POST /events/{id}/amend` | HTTP 400; advises change request | HTTP 400 Bad Request | **Passed** | Elnathan |
| **TC-106** | Negative Path | AC3: Rejected refused | Amending a Rejected request is refused | Event Rejected | `POST /events/{id}/amend` | HTTP 400; advises change request | HTTP 400 Bad Request | **Passed** | Elnathan |
| **TC-107** | Cross-Cutting | AC4: Ownership guard | Organiser cannot amend another's request | Event owned by Organiser A | Log in as Organiser B, `POST /amend` on event A | HTTP 403 Forbidden; event untouched | HTTP 403 Forbidden | **Passed** | Elnathan |
| **TC-108** | Negative Path | AC1: Draft refused | Amending a Draft is refused | Event in Draft | `POST /events/{id}/amend` | HTTP 400; advise to complete & submit | HTTP 400 Bad Request | **Passed** | Elnathan |
| **TC-109** | Workflow Variation | AC1: Amend while Returned | Organiser amends a Returned request | Event Returned | Coordinator returns, Organiser amends | HTTP 200; closes clarification loop | HTTP 200: Retains Returned status | **Passed** | Elnathan |
| **TC-110** | Cross-Cutting | Role Authorisation | Coordinator cannot amend an event | Logged in as coordinator | Coordinator calls `POST /events/{id}/amend` | HTTP 403 Forbidden; only organisers may amend | HTTP 403 Forbidden | **Passed** | Elnathan |
| **TC-111** | Cross-Cutting | Authentication | Unauthenticated amendment is refused | No session cookie | `POST /events/{id}/amend` with no session | HTTP 401 Unauthorized | HTTP 401: `{"detail": "Not authenticated"}` | **Passed** | Elnathan |
| **TC-112** | Negative Path | Errors Surfaced | Amending non-existent event | Logged in as organiser | `POST /events/999999/amend` | HTTP 404 Event not found | HTTP 404 Not Found | **Passed** | Elnathan |
| **TC-113** | Boundary Path | Invalid Values | Amendment with invalid values is refused | Event Submitted | Empty title, attendance 0, past date | Refused (400/422); row untouched | Validation errors raised | **Passed** | Elnathan |
| **TC-114** | Negative Path | Empty Body | Amendment with empty body `{}` refused | Event Submitted | `POST /events/{id}/amend` with `{}` | HTTP 400 asking for at least one field | HTTP 400 Bad Request | **Passed** | Elnathan |
| **TC-115** | Cross-Cutting | Privilege Escalation | Workflow fields cannot be injected | Event Submitted | Send `{"status": "Approved", "organiser_id": 2}` | Schema discards unauthorized workflow fields | Privilege escalation prevented | **Passed** | Elnathan |

---

### 4. SCRUM-51: In-App Notifications (Sprint 2 Story - Renee)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-501** | Happy Path | AC1: Notify on Submit | Organiser notified when request submitted | Logged in as org; 2 coordinators | `POST /events/{id}/submit` $\rightarrow$ `GET /notifications` | 1 unread 'submitted' notification for Organiser | HTTP 200: `kind: "submitted"`, `read: false` | **Passed** | Renee |
| **TC-502** | Boundary Path | AC1: Draft only | Saving a draft sends no notification | Logged in as org | `POST /events` (draft) $\rightarrow$ `GET /notifications` | No notifications created | HTTP 200: `[]` (empty list) | **Passed** | Renee |
| **TC-503** | Workflow Variation | AC1: Resubmission | Organiser resubmits returned request | Event Returned | Coordinator returns $\rightarrow$ Organiser resubmits | 2 'submitted' notifications (initial + resubmit) | HTTP 200: 2 'submitted' notifications | **Passed** | Renee |
| **TC-504** | Happy Path | AC2: Return with Note | Organiser notified of clarification note | Event Submitted | `POST /events/{id}/return` with note | 1 'returned' notification containing note | HTTP 200: `kind: "returned"`, note included | **Passed** | Renee |
| **TC-505** | Happy Path | AC3: Notify on Approval | Organiser notified upon approval | Event Submitted | `POST /events/{id}/approve` | 1 'approved' notification; no 'rejected' | HTTP 200: `kind: "approved"` | **Passed** | Renee |
| **TC-506** | Happy Path | AC3: Notify on Rejection | Organiser notified upon rejection with reason | Event Submitted | `POST /events/{id}/reject` with reason | 1 'rejected' notification containing reason | HTTP 200: `kind: "rejected"`, reason included | **Passed** | Renee |
| **TC-507** | Negative Path | Refused action | Refused rejections do not notify | Event Submitted | Wrong coordinator (403), blank reason (422) | No 'rejected' notification created | HTTP 403, 422; 0 notifications sent | **Passed** | Renee |
| **TC-508** | Negative Path | Refused action | Approving twice notifies only once | Event Submitted | `POST /approve` (200), then `POST /approve` (400) | Exactly 1 'approved' notification | HTTP 200, then HTTP 400; 1 notification | **Passed** | Renee |
| **TC-509** | Cross-Cutting | AC4: Entitlement | Unrelated organiser receives nothing | 2 organisers seeded | Event A approved $\rightarrow$ Organiser B checks notifications | Organiser B sees 0 notifications | HTTP 200: `[]` (empty list) | **Passed** | Renee |
| **TC-510** | Cross-Cutting | AC4: Entitlement (Unit) | `notify()` refuses unentitled recipient | Event owned by Organiser A | Call `notify(Organiser B, Event A)` | `NotEntitled` exception raised; 0 rows written | `NotEntitled` raised safely | **Passed** | Renee |
| **TC-511** | Cross-Cutting | AC4: Lost Access | Notifications hidden after reassignment | Coordinator has notification | Event reassigned $\rightarrow$ old coordinator checks | Old notification omitted from list | HTTP 200: `[]` | **Passed** | Renee |
| **TC-512** | Cross-Cutting | Authentication | Notifications require login | No active session | `GET /notifications` with no cookie | HTTP 401 Unauthorized | HTTP 401: `{"detail": "Not authenticated"}` | **Passed** | Renee |
| **TC-513** | Happy Path | Mark as Read | User marks own notification as read | Organiser has notification | `POST /notifications/{id}/read` | Notification shows `read == true` | HTTP 200: `{"read": true}` | **Passed** | Renee |
| **TC-514** | Cross-Cutting | Ownership Guard | User cannot mark another's notification | Organiser A has notification | Organiser B calls `POST /read` on A's note | HTTP 404 Not Found (no existence leak) | HTTP 404 Not Found | **Passed** | Renee |
| **TC-515** | Workflow Variation | Ordering | Notifications returned newest first | Event submitted then approved | Call `GET /notifications` | Order: approved, then submitted | Order: `["approved", "submitted"]` | **Passed** | Renee |

---

### 5. SCRUM-23: Lead Reassigns Coordinator (Sprint 2 Story - Renee)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-231** | Happy Path | AC1: Lead Role | Event Coordinator Lead can log in | Seeded `lead1` / `password123` | Call `POST /auth/login` | Login succeeds; lands on `/events` | HTTP 200: `role: "lead"`, `redirect_url: "/events"` | **Passed** | Renee |
| **TC-232** | Happy Path | AC1: Seed Data | Seed data includes a Lead account | None | Inspect `backend/seed_users.py` | `lead1` present with role `lead` | Source inspection passed | **Passed** | Renee |
| **TC-233** | Happy Path | AC2: Lead Visibility | Lead views every event and assigned coordinator | 2 submitted events | Log in as lead $\rightarrow$ `GET /events` | Both events returned with current coordinators | HTTP 200: All events visible | **Passed** | Renee |
| **TC-234** | Cross-Cutting | AC3: Lead Only Guard | Non-Lead users cannot reassign events | Event Submitted | Coord A, Coord B, Org call `POST /reassign` | HTTP 403 Forbidden for all non-lead roles | HTTP 403 Forbidden (x3) | **Passed** | Renee |
| **TC-235** | Cross-Cutting | Authentication | Unauthenticated reassignment refused | No active session | `POST /events/{id}/reassign` without cookie | HTTP 401 Unauthorized | HTTP 401 Unauthorized | **Passed** | Renee |
| **TC-236** | Happy Path | AC4 / AC6: Reassign & Log | Lead reassigns; single coordinator; logged | Event Submitted; logged in as lead | `POST /reassign` with new coordinator ID | Coordinator updated; audit record created | HTTP 200: `previous_coordinator_id`, audit logged | **Passed** | Renee |
| **TC-237** | Workflow Variation | AC6: Audit History | Reassigning twice maintains full history | Event Submitted | Reassign A $\rightarrow$ B, then B $\rightarrow$ A | 2 audit records preserved in chronological order | HTTP 200 (x2): 2 records verified | **Passed** | Renee |
| **TC-238** | Cross-Cutting | AC5: Management Moves | Former coordinator loses control; new one gains | Event reassigned A $\rightarrow$ B | Coord A approves (403); Coord B approves (200) | Permission transfers to new coordinator | A: HTTP 403, B: HTTP 200 (Approved) | **Passed** | Renee |
| **TC-239** | Workflow Variation | AC5: Work Lists | Coordinator dashboard lists update | Event reassigned A $\rightarrow$ B | Coord A and B call `GET /coordinator/events/data` | Absent from A's list; present in B's list | HTTP 200: Lists follow assignment | **Passed** | Renee |
| **TC-240** | Happy Path | AC7: Organiser View | Organiser sees the new coordinator | Event reassigned | Organiser calls `GET /events` | `assigned_coordinator_id` reflects new coordinator | HTTP 200: Updated coordinator shown | **Passed** | Renee |
| **TC-241** | Happy Path | AC8: 3-Way Notification | All 3 actors notified upon reassignment | Event reassigned | Lead calls `POST /reassign` | New coord (`reassigned_to_you`), org (`coordinator_changed`), old coord (`reassigned_away`) | All 3 notifications generated | **Passed** | Renee |
| **TC-242** | Workflow Variation | AC8: Former Notice | Former coordinator still sees departure notice | Event reassigned | Old coord calls `GET /notifications` | Sees `reassigned_away` notice (no event link) | HTTP 200: `reassigned_away` present | **Passed** | Renee |
| **TC-243** | Negative Path | Same Coordinator | Reassigning to current coordinator refused | Event Submitted | Reassign to currently assigned coordinator ID | HTTP 400 Bad Request; nothing changed | HTTP 400: Refused safely | **Passed** | Renee |
| **TC-244** | Negative Path | Inactive Target | Target must be an active coordinator | Event Submitted | Reassign to inactive coord / organiser / lead / 99999 | HTTP 400 Bad Request for all invalid targets | HTTP 400 Bad Request (x4) | **Passed** | Renee |
| **TC-245** | Negative Path | Draft Refused | Draft event cannot be reassigned | Event in Draft | Lead calls `POST /reassign` on Draft | HTTP 400 Bad Request | HTTP 400 Bad Request | **Passed** | Renee |
| **TC-246** | Negative Path | Finished Event Refused | Rejected / Completed event cannot be reassigned | Event Rejected / Completed | Lead calls `POST /reassign` | HTTP 400 Bad Request | HTTP 400 Bad Request (x2) | **Passed** | Renee |
| **TC-247** | Negative Path | Errors Surfaced | Reassigning non-existent event | Logged in as lead | `POST /events/424242/reassign` | HTTP 404 Event not found | HTTP 404 Not Found | **Passed** | Renee |
| **TC-248** | Negative Path | Empty Body | Reassignment without `coordinator_id` refused | Logged in as lead | `POST /events/{id}/reassign` with `{}` | HTTP 422 validation error | HTTP 422 Unprocessable Entity | **Passed** | Renee |

---

### 6. SCRUM-63: First Assignment Notification (Sprint 2 Story - Renee)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-631** | Happy Path | AC1: Organiser Told | Organiser notified when coordinator assigned | Logged in as org; 2 coordinators | Submit event $\rightarrow$ Organiser checks notifications | 1 `coordinator_assigned` note naming coordinator | HTTP 200: `kind: "coordinator_assigned"`, names coordinator | **Passed** | Renee |
| **TC-632** | Happy Path | AC3: Coordinator Told | Assigned coordinator notified; other is not | Event submitted | Both coordinators check `GET /notifications` | Assigned coord gets `assigned_to_you`; other gets none | HTTP 200: 1 for assigned; `[]` for other | **Passed** | Renee |
| **TC-633** | Workflow Variation | AC3: Round-Robin | Round-robin notifies each coordinator for own event | 2 events submitted | Coord A and B check notifications | Coord A sees Event 1; Coord B sees Event 2 | HTTP 200: Event IDs match assignments | **Passed** | Renee |
| **TC-634** | Boundary Path | Resubmission Guard | Resubmitting after return does not re-notify | Event Returned | Organiser resubmits event | No duplicate `coordinator_assigned` created | Exactly 1 notification each | **Passed** | Renee |
| **TC-635** | Negative Path | 0 Coordinators | Failed submission sends no assignment notification | 0 active coordinators | Attempt submit (400) | No assignment notifications sent | 0 notifications created | **Passed** | Renee |
| **TC-636** | Boundary Path | Draft Only | Saving a draft sends no assignment notification | Logged in as org | Create draft event | 0 notifications created | 0 notification rows | **Passed** | Renee |
| **TC-637** | Cross-Cutting | Entitlement | Unrelated organiser receives nothing | 2 organisers seeded | Org A submits event $\rightarrow$ Org B checks notifications | Organiser B sees 0 notifications | HTTP 200: `[]` | **Passed** | Renee |
| **TC-638** | Happy Path | AC2: Reassignment Link | Organiser notified on coordinator change via SCRUM-23 | Event reassigned | Lead reassigns coordinator | Organiser receives `coordinator_changed` | HTTP 200: `kind: "coordinator_changed"` | **Passed** | Renee |

---

### 7. SCRUM-30: Add Venue with Setup/Turnaround Buffers (Sprint 2 Story - Sarah)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-3001** | Happy Path | AC1 & AC4: Venue Creation | Venue Staff adds venue with full attributes & buffers | Logged in as `venue_staff` | `POST /venues` with capacity, layouts, 45m setup, 60m turnaround | Venue created (201), `is_active: true`, buffers stored | HTTP 201 Created: Full venue payload returned | **Passed** | Sarah |
| **TC-3002** | Workflow Variation | AC4: Default Buffers | Venue creation without buffer times defaults to standard | Logged in as `venue_staff` | `POST /venues` without buffer fields | Defaults to 30m setup, 45m turnaround | HTTP 201: `setup_time_mins: 30`, `turnaround_time_mins: 45` | **Passed** | Sarah |
| **TC-3003** | Happy Path | AC2: Search / Listing | Newly added venue is findable and retrievable | Logged in as `venue_staff`; venue added | Call `GET /venues` and `GET /venues/{id}` | Venue appears in list and matches on GET by ID | HTTP 200: Findable in listing & direct fetch | **Passed** | Sarah |
| **TC-3004** | Cross-Cutting | AC3: Role Guard | Non-Venue Staff cannot create venue records | Logged in as organiser / coordinator | `POST /venues` attempted by organiser & coordinator | Refused (403 Forbidden) with role message | HTTP 403 Forbidden (x2) | **Passed** | Sarah |
| **TC-3005** | Cross-Cutting | Authentication | Unauthenticated venue creation is refused | No session cookie | `POST /venues` with no session | HTTP 401 Unauthorized | HTTP 401: `{"detail": "Not authenticated"}` | **Passed** | Sarah |
| **TC-3006** | Negative Path | Conflict Guard | Duplicate venue name is refused with conflict | "Innovation Lab" exists | `POST /venues` with duplicate name (case-insensitive) | Request refused (409 Conflict) | HTTP 409 Conflict: `"...already exists"` | **Passed** | Sarah |
| **TC-3007** | Boundary Path | Boundary Validation | Capacity $\le 0$ is rejected | Logged in as `venue_staff` | `POST /venues` with `capacity: 0` and `capacity: -10` | Refused (422 Unprocessable Entity) | HTTP 422: Pydantic validation rejected | **Passed** | Sarah |
| **TC-3008** | Negative Path | Field Validation | Blank or whitespace-only name/location rejected | Logged in as `venue_staff` | `POST /venues` with blank name and location | Refused (422 Unprocessable Entity) | HTTP 422: Blank strings rejected | **Passed** | Sarah |
| **TC-3009** | Boundary Path | Boundary Validation | Negative buffer time is rejected | Logged in as `venue_staff` | `POST /venues` with `setup_time_mins: -15` | Refused (422 Unprocessable Entity) | HTTP 422: Non-negative buffer guard | **Passed** | Sarah |
| **TC-3010** | Negative Path | Wildcard Handling | Duplicate check ignores SQL wildcards `_` and `%` | "HallA1" exists | Create "Hall_1" (succeeds 201), duplicate "hall_1" (409) | "Hall_1" allowed; exact match rejected | HTTP 201 on "Hall_1", HTTP 409 on duplicate | **Passed** | Sarah |

---

### 8. SCRUM-33: Search & Filter Venues Against Requirements (Sprint 2 Story - Sarah)
| Test Case ID | Path Classification | Acceptance Criterion | Test Scenario | Pre-conditions | Test Steps & Test Data | Expected Result | Actual Result & Evidence | Status | Executed By |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-3301** | Happy Path | AC2: Capacity Filter | Filter venues by minimum capacity / attendance | Logged in as coordinator; venues seeded | `GET /venues/search?min_capacity=100` | Only venues with `capacity >= 100` returned | HTTP 200: Returns `["Grand Hall"]` | **Passed** | Sarah |
| **TC-3302** | Happy Path | AC2: Location Filter | Filter venues by location substring (case-insensitive) | Venues seeded | `GET /venues/search?location=building a` | Returns venues matching location substring | HTTP 200: `["Grand Hall", "Executive Boardroom"]` | **Passed** | Sarah |
| **TC-3303** | Happy Path | AC2: Layout Filter | Filter venues by supported room layout | Venues seeded | `GET /venues/search?layout=Banquet` | Returns venues supporting requested layout | HTTP 200: `["Grand Hall"]` | **Passed** | Sarah |
| **TC-3304** | Happy Path | AC2: Facilities Filter | Filter venues containing all required facilities | Venues seeded | `GET /venues/search?facilities=Projector,WiFi` | Returns venues possessing all required facilities | HTTP 200: Venues matching full facilities subset | **Passed** | Sarah |
| **TC-3305** | Happy Path | AC2: Accessibility | Filter venues by accessibility requirement | Venues seeded | `GET /venues/search?accessibility=wheelchair` | Returns wheelchair accessible venues | HTTP 200: Accessible venues returned | **Passed** | Sarah |
| **TC-3306** | Happy Path | AC3: Combined Filter | Multi-criteria search with simultaneous predicates | Venues seeded | Query with capacity + location + layout + facility | Applies AND logic across all filter criteria | HTTP 200: `["Executive Boardroom"]` | **Passed** | Sarah |
| **TC-3307** | Happy Path | AC1 / AC4: No Conflict | Date/time search on free day returns available | No booking on target date | `GET /venues/search` with free date/time window | All candidates return `is_available: true` | HTTP 200: `is_available == true`, reason null | **Passed** | Sarah |
| **TC-3308** | Negative Path | AC1 / AC4: Direct Conflict | Date/time directly overlaps with existing booking | Booking 14:00-17:00 | Search window 15:00-16:00 | Booked venue marked `is_available: false` | HTTP 200: `is_available == false`, conflict reason | **Passed** | Sarah |
| **TC-3309** | Boundary Path | AC1 / AC4: Turnaround Buffer | Search window falls inside turnaround buffer | Booking ends 17:00 (60m buffer) | Search window 17:30-19:30 | Venue marked `is_available: false` due to turnaround | HTTP 200: Turnaround buffer conflict surfaced | **Passed** | Sarah |
| **TC-3310** | Boundary Path | AC1 / AC4: Setup Buffer | Search window falls inside setup prep buffer | Booking starts 14:00 (60m setup) | Search window 11:00-13:30 (ends in setup) | Venue marked `is_available: false` due to setup buffer | HTTP 200: Setup buffer conflict surfaced | **Passed** | Sarah |
| **TC-3311** | Workflow Variation | AC4: Available Only | `available_only=true` excludes conflicting venues | Time conflict exists | `GET /venues/search?available_only=true` | Conflicting venues filtered out of response list | HTTP 200: Conflicting venues excluded | **Passed** | Sarah |
| **TC-3312** | Cross-Cutting | Inactive Venues | Retired/inactive venues excluded from search | Inactive venue in DB (`is_active: false`) | `GET /venues/search` | Inactive venue never returned | HTTP 200: Retired venue excluded | **Passed** | Sarah |
| **TC-3313** | Cross-Cutting | Authentication | Unauthenticated search request rejected | No session cookie | `GET /venues/search` without cookie | HTTP 401 Unauthorized | HTTP 401: `{"detail": "Not authenticated"}` | **Passed** | Sarah |
