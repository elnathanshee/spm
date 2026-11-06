import Alpine from "alpinejs";
import "./styles.css";
import "../app.js";
// [Sprint 5] B SCRUM-82 component import: insert directly below this line.
// [Sprint 5] C SCRUM-102 component import: insert directly below this line.
// [Sprint 5] D SCRUM-41 component import: insert directly below this line.
// [Sprint 5] E SCRUM-45 component import: insert directly below this line.
import { attendeeRegistration } from "./attendee-registration.js";
import { eventRegistrations } from "./event-registrations.js";
// [Sprint 5] F SCRUM-49 component import: insert directly below this line.

window.Alpine = Alpine;
// [Sprint 5] B SCRUM-82 Alpine.data: insert directly below this line.
// [Sprint 5] C SCRUM-102 Alpine.data: insert directly below this line.
// [Sprint 5] D SCRUM-41 Alpine.data: insert directly below this line.
// [Sprint 5] E SCRUM-45 Alpine.data: insert directly below this line.
Alpine.data("attendeeRegistration", attendeeRegistration);
Alpine.data("eventRegistrations", eventRegistrations);
// [Sprint 5] F SCRUM-49 Alpine.data: insert directly below this line.
Alpine.start();