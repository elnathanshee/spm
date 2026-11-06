import Alpine from "alpinejs";
import "./styles.css";
import "../app.js";
// [Sprint 5] B SCRUM-82 component import: insert directly below this line.
import { venueUnavailability } from "./venue-unavailability.js";
import { retiredVenues } from "./retired-venues.js";
// [Sprint 5] C SCRUM-102 component import: insert directly below this line.
// [Sprint 5] D SCRUM-41 component import: insert directly below this line.
import { equipmentCatalogue } from "./equipment-catalogue.js";
import { equipmentRequestActions, eventReservations } from "./equipment-reservations.js";
// [Sprint 5] E SCRUM-45 component import: insert directly below this line.
// [Sprint 5] F SCRUM-49 component import: insert directly below this line.

window.Alpine = Alpine;
// [Sprint 5] B SCRUM-82 Alpine.data: insert directly below this line.
Alpine.data("venueUnavailability", venueUnavailability);
Alpine.data("retiredVenues", retiredVenues);
// [Sprint 5] C SCRUM-102 Alpine.data: insert directly below this line.
// [Sprint 5] D SCRUM-41 Alpine.data: insert directly below this line.
Alpine.data("equipmentCatalogue", equipmentCatalogue);
Alpine.data("equipmentRequestActions", equipmentRequestActions);
Alpine.data("eventReservations", eventReservations);
// [Sprint 5] E SCRUM-45 Alpine.data: insert directly below this line.
// [Sprint 5] F SCRUM-49 Alpine.data: insert directly below this line.
Alpine.start();