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
| D2 | Related buttons sit together in one `.button-group`, in their original order. Separate controls form their own group on the right. All button rows use one 12px gap (`--gap-actions`). | Lead request cards: "Open request" and "Venue bookings" on the left; "Assigned coordinator" and "Assign coordinator"/"Save reassignment" on the right, shown only for Submitted, Under Review and Returned (`canAssignEvent`). Every button row uses the gap. | Gap: `tokens.css` `--gap-actions`; group: `components.css` `.button-group`; placement: `styles.css` `.lead-row-actions` | `46b239c` |
| D3 | Summary card labels start 12px (`--space-3`) after the coloured line. Numbers stay right-aligned. Long labels wrap between words. Cards wrap to fewer columns rather than squeeze. | Summary cards on Assignment oversight (Lead), Venue operations (Venue Staff) and Equipment worklist (Technical Support). The Coordinator's summary strip uses the same component without the line. | `components.css` "Summary cards" (`.summary-strip`, `.operations-summary`) | `46b239c`, `8bf1e3c` |
| D4 | The Organiser's request list has one section per status, headed by the status in plain words. Rows have no status badge. Empty sections are hidden. An unknown status goes to "Other statuses" and shows on its row. | Organiser "Your event requests". | `app.js` `ORGANISER_GROUPS`, `organiserGroups()` | `bc6a7cf` |
| D5 | The status badge stays in the request details panel. | Event request details panel. | `index.html` event detail template; badge style: `components.css` `.status-badge` | `bc6a7cf` |
| D6 | List rows: title and details on the left, buttons only on the right, all ending at the same edge. "View details" comes first, then the row's main action. "Submit for review" is the only filled button. On phones: details, then any note, then the buttons. | Organiser request rows. | `styles.css` `.request-row`, `.request-actions` (Organiser only, so it stays a page rule) | `bc6a7cf` |
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

## 3. ⚠ To confirm

1. **12px between buttons everywhere.** The design system used 8px. It became 12px in `46b239c` for the Lead cards and now applies to every button row.
2. **Rail by default under 1024px.** With no saved choice, screens from 768px to 1023px start with the sidebar as a rail.
3. **Overlay below 768px.** It used to start at 900px; it's now below 768px, as asked in `8bf1e3c`. Between 768px and 900px the sidebar stays in place.
4. **Rejection reason taken from notification text.** It's read from the wording of `notify_organiser_of_status`. If that wording changes, the reason disappears and the row says no reason is shown. This lasts until #10 is fixed.
5. **"Save as draft" kept.** Drafts can't be edited afterwards (#11), so saving a draft may not be useful.
6. **Section order** for the Organiser: Returned, Drafts, Submitted, Under review, Approved, Planning, Confirmed, Rejected, Completed, Cancelled. Planning, Confirmed, Completed and Cancelled can't occur yet, because nothing in the backend moves a request into them.
7. **"Returned for clarification" uses an attention colour** (orange heading); no other section is coloured.
8. **The collapsed rail hides the name, role and "Sign out" text.** They stay available to screen readers and as hover titles.
9. **`.request-row` and `.lead-row-actions` stay page rules.** Each is used by one screen. If Coordinator or Lead rows adopt the same layout, move them into `components.css`.
