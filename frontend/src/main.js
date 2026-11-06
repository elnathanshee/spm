import Alpine from "alpinejs";
import "./styles.css";
import "../app.js";
// [Sprint 5] B SCRUM-82 component import: insert directly below this line.
import { venueUnavailability } from "./venue-unavailability.js";
import { retiredVenues } from "./retired-venues.js";
// [Sprint 5] C SCRUM-102 component import: insert directly below this line.
import { eventLifecycle } from "./event-lifecycle.js";
import { reviewFlags } from "./review-flags.js";
import { safetyWorkspace } from "./safety-workspace.js";
// [Sprint 5] D SCRUM-41 component import: insert directly below this line.
// [Sprint 5] E SCRUM-45 component import: insert directly below this line.
// [Sprint 5] F SCRUM-49 component import: insert directly below this line.

window.Alpine = Alpine;
// [Sprint 5] B SCRUM-82 Alpine.data: insert directly below this line.
Alpine.data("venueUnavailability", venueUnavailability);
Alpine.data("retiredVenues", retiredVenues);
// [Sprint 5] C SCRUM-102 Alpine.data: insert directly below this line.
Alpine.data("eventLifecycle", eventLifecycle);
Alpine.data("reviewFlags", reviewFlags);
Alpine.data("safetyWorkspace", safetyWorkspace);
// [Sprint 5] D SCRUM-41 Alpine.data: insert directly below this line.
// [Sprint 5] E SCRUM-45 Alpine.data: insert directly below this line.
// [Sprint 5] F SCRUM-49 Alpine.data: insert directly below this line.
Alpine.start();