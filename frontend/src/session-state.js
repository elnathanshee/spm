export function resetSignedOutState(state) {
  state.user = null;
  state.currentPath = "/login";
  state.sidebarOpen = false;
  state.loading = false;
  state.accessDenied = false;
  state.unbuiltRole = false;
  state.events = [];
  state.assignedEvents = [];
  state.notificationOpen = false;
  state.allCoordinatorEvents = [];
  state.leadEvents = [];
  state.coordinators = [];
  state.notifications = [];
  state.bookingDialog = null;
  state.venueWorkspace = null;
  state.staffBooking = null;
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
  };
  state.amendForm = { title: "", description: "" };
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
