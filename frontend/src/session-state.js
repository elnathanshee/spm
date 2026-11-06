export function resetSignedOutState(state) {
  state.user = null;
  state.currentPath = "/login";
  state.sidebarOpen = false;
  state.loading = false;
  state.accessDenied = false;
  state.unbuiltRole = false;
  state.events = [];
  state.eventsView = { loaded: false, error: "", denied: false, query: "", group: "all", sort: "start", busyId: null };
  state.draftBusy = false;
  state.assignedEvents = [];
  state.notificationOpen = false;
  state.allCoordinatorEvents = [];
  state.assignedView = { loaded: false, error: "", denied: false, query: "", scope: "all", sort: "queue" };
  state.allEventsView = { loaded: false, error: "", denied: false, query: "", status: "all", sort: "submitted" };
  state.leadView = { query: "", filter: "all", status: "all", sort: "attention", loaded: false, error: "", denied: false, coordinatorsError: "" };
  state.assignmentState = {};
  state.picker = { eventId: null, query: "", active: 0 };
  state.bookingView = { query: "", sort: "start" };
  state.technicalView = { query: "", sort: "event" };
  state.leadEvents = [];
  state.coordinators = [];
  state.notifications = [];
  state.bookingDialog = null;
  state.venueCalendar = null; // [SCRUM-96] AC4: the next user on this computer must not see it
  state.venueWorkspace = null;
  state.staffBooking = null;
  state.technicalWorklist = null;
  state.eventDetail = null;
  state.decisionBusy = false;
  state.alert = null;
  state.fieldErrors = {};
  state.loginForm = { username: "", password: "" };
  state.showPassword = false;
  state.draft = {
    title: "",
    purpose: "",
    description: "",
    proposed_start: "",
    expected_attendance: null,
    venue_requirements: "",
    accessibility_needs: "",
    equipment_requirements: "",
    registration_needs: "",
  };
  state.amendForm = {
    title: "",
    purpose: "",
    description: "",
    proposed_start: "",
    expected_attendance: null,
    venue_requirements: "",
    accessibility_needs: "",
    equipment_requirements: "",
    registration_needs: "",
  };
  state.amendOriginal = null;
  state.decisionForm = { reason: "" };
  state.modal = { open: false, kind: "", event: null };
}

export function beginAuthentication(state) {
  if (state.loading) return false;
  state.loading = true;
  state.alert = { message: "", kind: "error" };
  return true;
}

export function finishAuthentication(state) {
  state.loading = false;
}
