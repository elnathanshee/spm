# UI decisions

The rules the ConnectSphere frontend follows, where each applies, and the file that controls it.
They come from the UI work on branch `improve-ui` after the design system (`6a0f79b`).

- **Guard tests:** `frontend/tests/ui-decisions.test.js` (UI-D1 to UI-D7) fails if one of the
  decisions in section 1 is undone. Other decisions have their own tests, named in the tables.
- **Changing a decision on purpose:** update this file and the guard test in the same commit.
- **⚠ Confirm:** marks a choice made without an explicit instruction. Please confirm or change it.

Files: `tokens.css` = `frontend/src/tokens.css`, `components.css` = `frontend/src/components.css`,
`styles.css` = `frontend/src/styles.css` (page layout only), `app.js` = `frontend/app.js`.

## 1. Fixes made after the design system

| # | Rule | Where it applies | Controlled by | Commit |
|---|---|---|---|---|
| D1 | A status badge directly above a details grid has 16px (`--space-4`) of space below it. | Event request details panel (every role that opens it). The same rule covers any `.status-badge` placed right before a `.booking-detail-grid`. | `components.css` "Detail grid" (`.status-badge + .booking-detail-grid`) | `46b239c` |
| D2 | Related buttons sit together in one `.button-group`, in their original order. Separate controls form their own group. All button rows use one 12px gap (`--gap-actions`). | Lead table (changed on purpose in the Lead refinement): the request opens from its title, so there's no "Open request" button. The assign or reassign controls sit in the Coordinator column, shown only for Submitted, Under Review and Returned (`canAssignEvent`); otherwise the Coordinator's name shows as text. "Venue bookings" is alone in the right-aligned Actions column. Every button row uses the gap. | Gap: `tokens.css` `--gap-actions`; group: `components.css` `.button-group`; placement: the Lead table in `index.html` | `46b239c`, Lead redesigns |
| D3 | Summary card labels start 12px (`--space-3`) after the coloured line. Numbers stay right-aligned. Long labels wrap between words. Cards wrap to fewer columns rather than squeeze. | Summary cards, wherever they are used. No page uses them at present: Assignment oversight, Venue operations and Equipment worklist have none, because theirs repeated list counts (see "Summary metrics"). The style stays for future cards. The Coordinator's summary strip uses the same component without the line. | `components.css` "Summary cards" (`.summary-strip`, `.operations-summary`) | `46b239c`, `8bf1e3c` |
| D4 | Request lists have one section per status, headed by the status in plain words. Rows have no status badge. Empty sections are hidden. An unknown status goes to "Other statuses" and shows on its row. The section that needs the user first is marked `.request-group-attention`. | Organiser "Your event requests"; Coordinator "Assigned requests". Not "All events", whose single list mixes statuses, so its rows keep the badge. | `app.js` `ORGANISER_GROUPS`, `COORDINATOR_GROUPS`; style: `components.css` "Request list" | `bc6a7cf`, Coordinator redesign |
| D5 | The status badge stays in the request details panel. | Event request details panel. | `index.html` event detail template; badge style: `components.css` `.status-badge` | `bc6a7cf` |
| D6 | List rows: title and details on the left, buttons only on the right, all ending at the same edge. When the buttons don't fit beside the details they move below them, still right-aligned. The row opens through a link on its title (see "Row links"); the Organiser still has a "View details" button, not changed yet. At most one filled button per row: "Submit for review" (Organiser), "Start review" or "Record decision" (Coordinator). On phones: details, then any note, then the buttons. | Organiser and Coordinator request rows, and Coordinator "All events". | `components.css` "Request list" (`.request-row`, `.request-actions`), "Row link" | `bc6a7cf`, Coordinator redesign, Lead refinement |
| D7 | The sidebar control is a quiet 40×40px icon button beside the breadcrumb, labelled "Collapse sidebar" or "Expand sidebar".<br>**768px and up:** a panel icon collapses the sidebar to a 72px rail, and the choice is kept in `localStorage`.<br>**Below 768px:** a menu icon opens it over the page; focus moves to the first visible item, and Escape returns focus to the control. | Every signed-in screen. | Button: `components.css` `.icon-control`; widths: `tokens.css` `--sidebar-width-*`; breakpoint: `app.js` `COMPACT_NAV_QUERY` and the 767/768px media queries in `styles.css`; behavior: `app.js` `toggleSidebar()`, `sidebarExpanded()` | `8bf1e3c` |
| D8 | All dates and times use one formatter, shown in Singapore time ("4 Nov 2026, 11:55 am"), whatever the browser's time zone. A same-day range shows the date once. | Every role. | `app.js` `DISPLAY_TIME_ZONE`, `parseServerTime`, `formatDateTime`, `formatRange`, and the wrappers `formatDate`, `formatUtc`, `formatSchedule`, `formatHoldExpiry`, `formatNotificationTime` | `6a0f79b` |
| D9 | The time zone is shown once, as a "Times in SGT" pill in the top bar.<br>**⚠ Not done:** per-value "SGT" labels and a "Singapore Time (GMT+8)" tooltip and input help text. Blocked by `BACKEND_ISSUES.md` #12: the backend doesn't return reliable Singapore times. | Top bar, every role. | `index.html` `.timezone-note`; style: `styles.css` `.timezone-note` | `8bf1e3c` (logged) |

### Status of the requested fixes

All of D1 to D8 are in the code. D9's per-value labels are **missing on purpose** (blocked by #12).

## 2. Other UI decisions from recent commits

| Rule | Where | Controlled by | Commit |
|---|---|---|---|
| Colours, type sizes, spacing and radii come from tokens only. `styles.css` holds no raw colours or font sizes. | Everywhere | `tokens.css`; test TC-9801 | `6a0f79b` |
| One sans typeface. The serif is only for the sign-in display headings. | Everywhere | `tokens.css` `--font-display`; test TC-9802 | `6a0f79b` |
| One tone per backend status, the same for events and bookings. A badge always shows the status text. | Every status badge | `app.js` `STATUS_TONES`, `statusClass()`; test TC-9803 | `6a0f79b` |
| Initials come from the first letters of the first two words, otherwise the first two letters. | Avatar, venue tiles | `app.js` `initials()`; test TC-9805 | `6a0f79b` |
| Success messages are toasts that clear after 6 seconds. Errors stay inline until dismissed. | Every role | `app.js` `showAlert()`; `components.css` `.toast` | `6a0f79b` |
| Every focusable element shows the double focus ring. Text meets WCAG AA (4.5:1); control borders meet 3:1. | Everywhere | `tokens.css` `--focus-ring`; `components.css` `:focus-visible`; test TC-9807 | `6a0f79b` |
| The header is solid, with no blur behind it. | Top bar | `styles.css` `.topbar` | `8af09a0` |
| No "Workspace" box in the sidebar. The user's name and role appear once, in the sidebar footer. | Sidebar | `index.html` `.role-card` | `8af09a0` |
| A notification opens its booking (Venue Staff) or event request (Organiser, Coordinator, Lead) when it has one. Otherwise it's plain text. No "Mark all as read", because the backend has no bulk route (#9). | Notification panel | `app.js` `notificationTarget()`; test TC-9811 | `8af09a0` |
| The sign-in page fits the window with no page scrollbar. Fields lock while signing in. The backend's error shows inline. | Sign-in | `styles.css` `.login-shell`, `.login-fields` | `8af09a0` |
| Filter chips are one shared component with counts, only where the data loaded on the screen supports them. | Organiser list, Venue Staff booking filters | `components.css` `.filter-chips`, `.booking-filters` | `27b852c` |
| A Returned request shows the Coordinator's clarification request. A Rejected request shows its reason from the rejection notification (the backend returns no reason field, #10). | Organiser list, amend dialog | `app.js` `rejectionReason()`; test TC-9903 | `27b852c` |
| A Returned request is updated and resubmitted from one dialog ("Save and resubmit"). | Organiser amend dialog | `app.js` `amendEvent(..., { resubmit })`; test TC-9907 | `27b852c` |
| The create form's main action is "Submit for review"; "Save as draft" is secondary. Field errors come from the backend, and focus moves to the first one. | Organiser create form | `app.js` `createEventDraft(..., { submit })`; test TC-9906 | `27b852c` |
| Organiser section descriptions only say what is true for every request in the section. For example, Submitted doesn't claim a Coordinator. | Organiser list | `app.js` `ORGANISER_GROUPS`; test TC-9901 | `bc6a7cf` |
| Values in row details (dates, names) stay on one line. Rows wrap between items. | Coordinator and Lead rows, Organiser rows | `styles.css` `.inline-meta dd`, `.request-meta dd` | `8bf1e3c`, `27b852c` |
| The notification bell is the same quiet icon button as the sidebar control. | Top bar | `components.css` `.icon-control` | `8bf1e3c` |

### Coordinator screens

| Rule | Where | Controlled by |
|---|---|---|
| Three filters, in this order: All (the default), Active, Completed. Completed means Rejected, Completed or Cancelled; everything else is active. All shows every assigned request once, under the same status sections, with the sections needing a decision first. Counts follow the list-controls rule (section 4). | Assigned requests | `app.js` `COORDINATOR_GROUPS` (`active`), `coordinatorScopeChips()`, `coordinatorInScope()`; tests TC-9931, TC-9932 |
| Sections keep the backend's queue order: awaiting first review, longest wait first, then by proposed date (`coordinator.assigned_requests`). | Assigned requests | `app.js` `coordinatorGroups()` |
| Counts appear only on the filters and section headings; there is no summary row. Search, sort and the no-match state come from the shared list controls (section 4). | Assigned requests | `index.html` Coordinator template; test TC-9701 |
| One main action per row, from the backend's rules: "Start review" for Submitted; "Record decision" for Under Review, which opens the details panel holding Approve, Request clarification and Reject. No main action for other statuses or for unassigned requests. | Assigned requests | `app.js` `coordinatorPrimary()`, `canDecideEvent()`; test TC-9933 |
| "Venue bookings" follows the venue booking rule in section 4. | Assigned requests | `app.js` `canBookEvent()`; test TC-9934 |
| Search, filters and sort for both screens are listed in section 4, "List controls". | Both Coordinator screens | `app.js` `listControls()` |
| Loading, load-error (keeping the list already shown), permission-denied and empty states. | Both Coordinator screens | `app.js` `recordListResult()`; test TC-9935 |

### Lead screen

| Rule | Where | Controlled by |
|---|---|---|
| A real table with the columns Request, Proposed, Status, Coordinator, Actions, so every cell sits under its header. Request: the title is a link that opens the request, with the ID, organiser and guests below it. Coordinator: the coordinator picker and Assign or Reassign when the backend allows assignment, otherwise the name as text. Actions: "Venue bookings" only, right-aligned. On phones each row is a card with a label on every cell. | Assignment oversight | `index.html` Lead template; `components.css` "Data table", "Row link"; `styles.css` "Lead: assignment oversight" |
| "Needs attention" is the default order: requests that need a Coordinator (unassigned and assignable), then requests past their proposed date that still await a decision (Submitted, Under Review, Returned), then the rest. Each group keeps the backend's order (earliest submitted). Those rows have a left accent. Past-date rows have a "Proposed date passed" flag; there's no "Needs assignment" tag, because the filter and the order already show it. | Assignment oversight | `app.js` `leadAttentionRank()`, `leadNeedsAssignment()`, `leadDatePassed()`; test TC-9954 |
| "Assign" or "Reassign", with "Cancel", appear only when the chosen Coordinator differs from the saved one (changed on purpose: the button used to show disabled). The backend refuses a same-Coordinator reassignment. While saving it reads "Saving…". Success hides both buttons and shows the confirmation in their place ("Assigned to coord1.") and as a toast. A refusal shows the backend's message on the row only and keeps both buttons. See "Inline changes". | Assignment oversight | `app.js` `assignmentChanged()`, `assignmentActionsShown()`, `saveAssignment()`, `cancelAssignment()`, `assignmentState`; tests TC-9951, TC-9952, TC-9981 to TC-9984 |
| A choice not saved yet survives the 15-second refresh, unless the request's assignment changed meanwhile. | Assignment oversight | `app.js` `fetchLeadData()`; test TC-9953 |
| Coordinator workload: a collapsible section, collapsed by default, holding a table of Coordinator and Open requests, fewest first and then by name. Its height is capped, with internal scrolling and a sticky header. Open requests are every status except Rejected, Completed and Cancelled, counted from `GET /lead/events`, which holds every non-Draft event (a Draft has no Coordinator). `GET /lead/coordinators` returns no workload field. | Assignment oversight | `app.js` `coordinatorsByWorkload()`, `coordinatorOpenCount()`; `components.css` "Disclosure", "Data table"; test TC-9955 |
| Loading, load-error (keeping the list shown), permission-denied and empty states. If the Coordinator list fails to load, a message says assignment is unavailable. | Assignment oversight | `app.js` `fetchLeadData()`, `recordListResult()`; test TC-9956 |

## 3. ⚠ To confirm

1. **12px between buttons everywhere.** The design system used 8px. It became 12px in `46b239c` for the Lead cards and now applies to every button row.
2. **Rail by default under 1024px.** With no saved choice, screens from 768px to 1023px start with the sidebar as a rail.
3. **Overlay below 768px.** It used to start at 900px; it's now below 768px, as asked in `8bf1e3c`. Between 768px and 900px the sidebar stays in place.
4. **Rejection reason taken from notification text.** It's read from the wording of `notify_organiser_of_status`. If that wording changes, the reason disappears and the row says no reason is shown. This lasts until #10 is fixed.
5. **"Save as draft" kept.** Drafts can't be edited afterwards (#11), so saving a draft may not be useful.
6. **Section order** for the Organiser: Returned, Drafts, Submitted, Under review, Approved, Planning, Confirmed, Rejected, Completed, Cancelled. Planning, Confirmed, Completed and Cancelled can't occur yet, because nothing in the backend moves a request into them.
7. **"Returned for clarification" uses an attention colour** (orange heading); no other section is coloured.
8. **The collapsed rail hides the name, role and "Sign out" text.** They stay available to screen readers and as hover titles.
9. **`.lead-row-actions` stays a page rule.** It's used by one screen. The request list (`.request-row` and related rules) moved to `components.css` when the Coordinator rows adopted it.
10. **Resolved: Venue bookings by request status.** See section 4 for the final rule. Still open: making finished requests view-only, which needs a backend rule first.
11. **Resolved: Coordinator counters removed.** They repeated the filter and section counts (see the summary metric rule in section 4).
12. **"At risk" means the proposed date has passed while the request still awaits a decision.** The backend has no
    risk field, so this is the only data-backed signal. The date comparison inherits the time zone uncertainty in
    `BACKEND_ISSUES.md` #12, so it can be off by up to 8 hours near the boundary.
13. **Workload counts open requests, meaning everything but Rejected, Completed and Cancelled.** Approved, Planning
    and Confirmed count as open because the Coordinator still plans them.

## 4. Rules

### Summary metrics

**Don't show a summary metric that repeats a filter or section count. Use one only if it adds
decision-making information not visible in the list.**

- **Applies to:** every role's list pages.
- **Follows it:** Organiser requests, Coordinator assigned requests and Coordinator all events.
  Their counts appear only on filters and section headings. Guarded by UI-D8.
- **Removed with the shared list controls**, because they repeated a chip count:
  - Lead: the "Needs assignment" and "Assigned events" cards, the "N need assignment" header pill,
    and the list's "N requests" total.
  - Venue Staff: the "Pending requests" card.
- **Removed in the Venue Staff redesign:** "Active venues" (repeated the catalogue's "N total") and "Unread updates"
  (repeated the bell's badge). Venue operations has no summary cards now. Guarded by TC-9976.
- **Kept:** the Lead's coordinator workload (open requests per Coordinator), now a collapsed table rather than cards,
  adds information that isn't in the list.
- **Removed in the Technical Support redesign:** "Events" and "Equipment lines" (repeated the number of event groups
  and lines). Each section heading now gives its event and line counts. Guarded by TC-9992.
- **No page breaks it now.**

### Venue bookings by request status

- **Backend rule** (checked in `venues.py`, `events.py` and `venue_holds.py`):
  - an assigned Coordinator can view an event's bookings (`get_event_bookings`), search venues
    (`require_venue_searcher`), request a venue (`submit_venue_booking_request`) and set a hold
    warning on their own live hold (`set_hold_warning`), in **every** request status;
  - a Coordinator who isn't assigned is refused (403).
- **Frontend rule:**
  - **Booking changes are allowed in every status**, so the button shows for every status on an
    assigned request.
  - **No access:** the button is hidden (`canBookEvent` requires the assignment). On the
    Coordinator pages every request is assigned to the Coordinator; "All events" has no
    "Venue bookings" button.
- **⚠ Flagged, not applied:**
  - **View-only bookings for finished requests (Rejected, Completed, Cancelled).** The backend
    allows changes in those statuses, so a view-only page would block actions it permits. It
    needs a backend rule first (`BACKEND_ISSUES.md` #5).
  - **Hiding the button for finished requests with no bookings.** The list
    (`GET /coordinator/events/data`) carries no booking count. Checking would take a
    `GET /events/{id}/bookings` call per row, which is out of scope here. The button stays.

### List controls

**Every list that people search or narrow uses the one list-controls component.** It has search on the left, with
its label above the field, and sort on the right when the list has a useful order. Search keeps at least 18rem;
when both don't fit, sort moves below it. Filter chips with counts sit
on the row below. **"All" is always the first chip and the default selection**; the other chips follow the
workflow order. On phones the search and sort stack, and the chips scroll in one row.

- **Controlled by:** `app.js` `listControls(key)` (one config per list) and `clearListSearch(key)`; markup:
  the identical `.list-controls` block in `index.html`, mounted with its key; style: `components.css`
  "List controls" and "Filter chips". Guarded by TC-9941 and TC-9942.
- **Behavior, the same on every list:**
  - Chip counts are the records each filter contains **before search**. (They followed the search before
    this change; changed on purpose.)
  - Search applies within the selected filter. The number of matches is announced to screen readers.
  - When nothing matches, the empty state says what wasn't found and in which filter, and offers "Clear search",
    which returns focus to the search box. Escape in the search box also clears it.
  - Sorting applies within each section where sections exist, and never moves a section.
  - Placeholders name only the fields that search covers.
  - No summary metric repeats a chip or section count (see "Summary metrics").

| Role and list | Search covers | Filters ("All" first) | Sort | Notes |
|---|---|---|---|---|
| Organiser: Your event requests | Title, purpose, description, #ID | All, then one chip per status section that has requests | "Earliest proposed date"; "Recently submitted"; "Recently created" | Unchanged except counts are before search. |
| Coordinator: Assigned requests | Title, organiser, #ID | All (default), Active, Completed | "Longest waiting" (the backend's queue order); "Earliest proposed date" | Sections needing a decision come first in All and Active. Sorting is within each section. |
| Coordinator: All events | Title, organiser, coordinator, #ID | All, then each status present, in workflow order | "Earliest submitted" (the backend's order); "Earliest proposed date" | One list without sections, so rows keep their status badge. |
| Lead: Assignment oversight | Title, organiser, coordinator, #ID | All, Needs assignment (no Coordinator yet), Assigned | "Needs attention" (default); "Earliest submitted" (the backend's order); "Earliest proposed date" | A table, not sections, so rows keep their status badge. |
| Venue Staff: Booking requests | Event, venue, booking #ID | All, Pending, Confirmed, Rejected, Expired | "Earliest booking date"; "Soonest hold expiry" | Placed inside the booking panel (`.list-controls-inset`). Then split into Needs a decision, Upcoming confirmed, Past and closed; sorting is within each. |
| Technical Support: Equipment worklist | Event, equipment, technical requirements, recorded by, event #ID | **None**: every line is the same kind of read-only record, so there is only one meaningful option | "Earliest event date" (the backend's order); "Recently recorded" (sorts events and the lines within each) | Lines stay grouped by event, and events by date section; sorting is within each section. |
| Attendee: Event directory | **None** | **None** | **None** | No list exists: the backend has no attendee event list (`BACKEND_ISSUES.md` #1). |
| Venue Staff: Venue catalogue | **None** | **None** | **None** | A short reference list of active venues on the same page; controls wouldn't help. |

**⚠ Confirm:** Coordinator "All" as the default (it was "Active") and counts before search (they followed the search)
both change earlier decisions, as asked.

### Sort names

**Sort controls use the label "Sort by". Each option is one standalone phrase in sentence case, with no colons or
commas. It must make the sort field and direction clear and match what the sort actually does. Use the same name
for the same sort across all roles. Examples: "Earliest proposed date", "Recently submitted", "Longest waiting".**

**Keep sort names concise, ideally 1-3 words. Name only the main sort. Don't include tie-breakers, such as
"then earliest proposed date", in the label.**

- **Controlled by:** the `sorts` and `sortLabel` of each config in `app.js` `listControls()`. Guarded by TC-9946.
- **Missing dates:** a request without the sorted date goes after the requests that have one, in either
  direction (`byAsc`, `byDesc` in `app.js`). For example, a Draft has no submitted date, so it comes last under
  "Recently submitted". Guarded by TC-9947.
- **Final names:**

  | Role and list | Sort options |
  |---|---|
  | Organiser: Your event requests | Earliest proposed date · Recently submitted · Recently created |
  | Coordinator: Assigned requests | Longest waiting · Earliest proposed date |
  | Coordinator: All events | Earliest submitted · Earliest proposed date |
  | Lead: Assignment oversight | Needs attention · Earliest submitted · Earliest proposed date |
  | Venue Staff: Booking requests | Earliest booking date · Soonest hold expiry |
  | Technical Support: Equipment worklist | Earliest event date · Recently recorded |

- **Coordinator "Longest waiting"** names the main sort of the backend's queue order
  (`coordinator.assigned_requests`): requests awaiting first review by submission time, oldest first. Every other
  request then follows by proposed date, earliest first. That second part isn't in the name, per the rule above.
  `GET /coordinator/events/data` doesn't return `submitted_at`, so sections other than "Awaiting first review" can't be
  sorted by waiting time; in those sections this option orders the same way as "Earliest proposed date".
- **Every name is 1-3 words with no tie-breaker.** Guarded by TC-9946.

### Row links, row actions, tables and lists that grow

- **List rows open through a link on the title. Don't add a separate open or view button.**
  - **Applies to:** Coordinator assigned requests and all events, the Lead table, and the Venue Staff booking table
    (the event name opens the booking review; the fragment is `#booking-<id>`). Controlled by `components.css`
    "Row link" (`.row-link`: hover underline, focus ring) and guarded by UI-D9. The link is an `<a>` with a
    `#request-<id>` fragment that opens the request details panel. There is no separate page for a request.
  - **⚠ Still using a separate button, not changed yet:** the Organiser's request rows ("View details").
- **Keep frequent row actions visible. Don't use an overflow menu for two or fewer actions.** No list has an
  overflow menu.
- **Table cells must align with their column headers.** Tables are real `<table>` elements (`components.css`
  "Data table"). On phones a table's rows become cards, and each cell shows its column name from `data-label`.
  Guarded by UI-D9.
- **Don't use large number cards for people or other lists that can grow. Use a compact list or table.** The
  Lead's coordinator workload is a table. No summary cards remain.

### Coordinator picker

A button labelled "Select coordinator" (or the chosen name) opens a panel with a search field, then a list showing
about six coordinators at a time and scrolling for the rest.

- **Options:** each reads "coord1 (1 open request)", with "requests" for any other number. They are ordered by fewest
  open requests, then by name. Names and counts come from `GET /lead/coordinators` and `GET /lead/events`. A
  `display_name` is used if the backend ever returns one; it returns only `username` today.
- **Search** filters by name as the user types. When nothing matches, the panel says
  "No coordinators match “x”".
- **Current Coordinator:** marked "Current".
- **Choosing** only sets the row's pending choice. Nothing is saved until Assign or Reassign.
- **Keyboard and screen readers:** the search is a combobox over a listbox, with the active option as
  `aria-activedescendant`. Arrow keys, Home and End move; Enter chooses; Escape closes and returns focus to the
  button; Tab closes; a click outside closes.
- **Controlled by:** `app.js` `openPicker`, `closePicker`, `pickerOptions`, `pickerKeydown`, `choosePickerOption`;
  `components.css` "Picker". It needs no library. Guarded by TC-9957 and TC-9958.

### Inline changes

**Show save actions for inline changes only after a value changes, with a way to cancel.**

- **Applies to:** the Lead's Coordinator column on Assignment oversight.
- **Before a change:** no buttons. The value shown is the saved one.
- **After a change:** "Assign" (no Coordinator saved yet) or "Reassign", filled, and "Cancel". Choosing the saved
  Coordinator again hides them. Nothing is sent until Assign or Reassign.
- **Cancel** (or Escape on either button) puts back the saved value, clears any error, and sends nothing.
- **Saved:** both buttons hide, and the existing confirmation shows in their place. **Failed:** the change stays
  unsaved, both buttons stay, and the backend's message shows below.
- **No movement:** the buttons and the confirmation share one slot as wide as "Reassign" and "Cancel". Hidden
  buttons use `visibility: hidden`, so they keep their space and leave the tab order.
- **Focus:** choosing a Coordinator returns focus to the picker button, and Tab reaches the save button next.
  Cancel and a successful save return focus to the picker button. A failed save keeps focus on the save button.
- **Controlled by** `app.js` `assignmentActionsShown()`, `cancelAssignment()`, `saveAssignment()`; `styles.css`
  `.lead-assign-slot`, `.lead-assign-actions`. Guarded by TC-9981 to TC-9984.

### Forms, connected tasks and form actions

- **Validate required inputs before making an API call, and show errors beside the relevant fields.**
  - Buttons stay enabled so people can see what's missing. Only a running request disables its button, which
    then shows "Searching…", "Checking…" or "Submitting…" and can't be sent twice.
  - Focus moves to the first field with an error. A form-level error (nothing entered) focuses the first field.
  - **Venue bookings dialog**, from the backend's rules (`venues.search_venues`, `suitability.venue_suitability`,
    `VenueBookingRequest`):

    | Button | Calls | Requires before calling |
    |---|---|---|
    | Search venues | `GET /venues/search` | At least one of start, end, min capacity, location, layout, accessibility, facilities. Start and end together or neither, end after start. Min capacity a whole number of 1 or more. "Available only" needs a start and end. |
    | Check suitability | `GET /events/{id}/venue-suitability` | Facilities to check, or "No facilities required" (sends an empty list). Left out, the backend reports every venue as at best "unknown". |
    | Submit booking request | `POST /venues/{id}/bookings` | A venue, a start and an end, with the end after the start and the start in the future. |

  - **⚠ Confirm:** requiring at least one search field is a frontend rule. The backend accepts an empty search
    and lists every venue.
  - Controlled by `app.js` `validateVenueSearch()`, `checkSuitability()`, `submitBooking()`, `focusFirstError()`.
    Guarded by TC-9961 to TC-9964.
- **Connected tasks should carry information forward instead of asking users to re-enter it.**
  - "Request this venue" on an available search result selects that venue and copies the searched start and end
    into the request form. It then focuses the first empty field, or "Submit booking request". Nothing is sent
    until that button is pressed. Unavailable results don't offer it.
  - Controlled by `app.js` `selectVenueFromSearch()`, `canRequestFromSearch()`. Guarded by TC-9965.
- **Form actions need clear space from surrounding sections.**
  - The dialog's sections (Booking history; Find and assess venues; "Request a venue", or "Request another venue"
    once there are bookings) share one heading style.
  - Each section's actions sit together behind a divider, with at least 16px above them, and never touch the end
    of the form or section.
  - Controlled by `styles.css` `.dialog-section`, `.dialog-section-heading`, `.dialog-form-actions`.

### Venue Staff: booking requests and retiring a venue

From `backend/venues.py`: `booking_detail_dict`, `_decide_booking`, `retire_venue`, `require_venue_staff`.

- **Sections, in the order they need attention.** The status chip, search and sort apply first, then:

  | Section | Contains | Why |
  |---|---|---|
  | Needs a decision | Every Pending booking, whatever its date | Pending is the only status the backend lets Venue Staff decide. One whose time has passed is still Pending and decidable, so it stays here, flagged "Booking time passed". |
  | Upcoming confirmed | Confirmed, not yet ended | These still hold the venue. |
  | Past and closed | Rejected, Expired, and Confirmed bookings that have ended | The record. No actions. |

  Empty sections are hidden. Rows keep their status badge because "Past and closed" mixes statuses, and so every
  table reads the same.
- **One table per section, with shared column widths** (Booking, Date and time, Status, Actions), so columns line up
  from one section to the next. Below 54rem the rows become cards (the shared data table).
- **Each row:** the event name links to the booking review; below it the booking #ID, venue and requester. Date and
  time is the booking range, plus the hold expiry for Pending and Expired bookings. A setup or turnaround clash
  (`conflict_flag`) shows as "Possible clash" with the backend's reason.
- **One filled button per Pending row: Approve.** It calls `POST /venues/bookings/{id}/approve` from the row. A
  refusal (409: hold expired, already decided, or a clash) shows under the buttons, and the list reloads with the
  current status. **Reject** is secondary: a reason is required, so it opens the review with the reason field
  focused. Other rows say "No action". Guarded by TC-9972 and TC-9973.
- **No capacity flag.** The booking list and review have no event attendance (`BACKEND_ISSUES.md` #6), and Venue
  Staff can't call the suitability check, so capacity conflicts can't be shown.
- **Retire asks first and says what it does.** Coordinators and Leads can no longer find or request the venue, and it
  leaves the catalogue. Nothing is deleted, and nothing in the app reactivates a venue. The confirmation lists the
  venue's Pending and upcoming Confirmed bookings from the loaded list (what the backend returns as
  `affected_bookings`). It says they aren't cancelled or notified and will leave Booking requests. If that venue's
  bookings didn't load, it says so instead of claiming there are none. Nothing is sent until "Retire venue".
  Guarded by TC-9975.
- **Page states:** "Loading venues and bookings…"; an error with "Try again" (a failed refresh keeps the page and
  says "Couldn't refresh…"); a permission-denied card on 403; a warning when some venues' bookings didn't load;
  empty and no-match states in the booking panel. Guarded by TC-9976.
- **Controlled by** `app.js` `VENUE_BOOKING_GROUPS`, `venueBookingGroups()`, `approveBookingRow()`,
  `rejectBookingRow()`, `retireImpact()`, `fetchVenueWorkspace()`; `styles.css` "Venue Staff booking requests".

### Technical Support: equipment worklist

From `backend/equipment.py` and `events.py`: Technical Support can read `GET /equipment-requests` (every event) and
`GET /events/{id}/equipment-requests`, and nothing else. There's no status to update, no work assigned to a person,
no event status, and `GET /events/{id}` is refused (`BACKEND_ISSUES.md` #14).

- **Sections by event date, in Singapore days, the only urgency in the data:** "Today and the next 7 days" (marked
  as needing attention), "Later", "Date not set", "Past events". Empty sections are hidden; sorting is within each
  section. Each heading gives its event and line counts.
- **Each event:** its title and #ID, the proposed date, and for the first section "Today", "Tomorrow" or
  "In N days". Its lines are a table (Equipment, Quantity, Technical requirements, Recorded: who and when), with
  fixed column widths so tables line up from one event to the next. Below 54rem the rows become cards.
- **No row actions and no title link.** The role can't open an event or change a line, so there's nothing to link
  to and no button to offer. The only control is Refresh.
- **Page states:** "Loading the worklist…"; an error with "Try again" (a failed refresh keeps the worklist and says
  "Couldn't refresh…"); a permission-denied card on 403; "No equipment recorded yet"; the shared no-match state.
- **⚠ Confirm:** the 7-day window is a frontend choice. The backend only orders by date.
- **⚠ Conflicts with the request, kept as is:** "one clear primary action per item" and "status updates" can't be
  met. The backend has no action for this role (`BACKEND_ISSUES.md` #14), and the rule against inventing UI for
  unsupported actions wins.
- **Controlled by** `app.js` `TECHNICAL_GROUPS`, `technicalSections()`, `technicalWhen()`, `technicalDaysAway()`,
  `fetchTechnicalWorklist()`; `styles.css` "Technical Support: equipment worklist". Guarded by TC-9991 to TC-9993.
