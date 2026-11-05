export const NAVIGATION = [
  { path: "/events", label: "My event requests", role: "Organiser", icon: "calendar" },
  { path: "/events/new", label: "Create request", role: "Organiser", icon: "plus" },
  { path: "/coordinator/events", label: "Assigned requests", role: "Coordinator", icon: "inbox" },
  { path: "/coordinator/all-events", label: "All events", role: "Coordinator", icon: "calendar" },
  { path: "/lead/events", label: "Assignment oversight", role: "Event Coordinator Lead", icon: "people" },
  { path: "/venue", label: "Venue operations", role: "Venue Staff", icon: "building" },
  { path: "/technical", label: "Technical support", role: "Technical Support", icon: "equipment" },
  { path: "/attendee", label: "Event directory", role: "Attendee", icon: "ticket" },
];

const NORMALIZED_ROLES = {
  organiser: "Organiser",
  coordinator: "Coordinator",
  lead: "Event Coordinator Lead",
  "venue staff": "Venue Staff",
  venue_staff: "Venue Staff",
  "technical support": "Technical Support",
  technical_support: "Technical Support",
  attendee: "Attendee",
};

const ROLE_PATHS = {
  Organiser: "/events",
  Coordinator: "/coordinator/events",
  "Event Coordinator Lead": "/lead/events",
  "Venue Staff": "/venue",
  "Technical Support": "/technical",
  Attendee: "/attendee",
};

// Paths that show the generic "not built yet" page. None are left: the Attendee page is its own screen now.
const PLACEHOLDER_PATHS = new Set();

export function normalizeRole(role) {
  return NORMALIZED_ROLES[String(role || "").trim().toLowerCase()] || role;
}

export function defaultPathForRole(role) {
  return ROLE_PATHS[normalizeRole(role)] || "/events";
}

export function canAccess(targetRole, userRole) {
  return normalizeRole(targetRole) === normalizeRole(userRole);
}

export function resolveRolePath(path, userRole) {
  const item = NAVIGATION.find((entry) => entry.path === path);
  const accessDenied = !item || !canAccess(item.role, userRole);
  return {
    accessDenied,
    currentPath: accessDenied ? "/access-denied" : path,
    unbuiltRole: !accessDenied && PLACEHOLDER_PATHS.has(path),
  };
}

export function requestedFrontendPath(hash) {
  return String(hash || "").startsWith("#/") ? String(hash).slice(1) : null;
}
