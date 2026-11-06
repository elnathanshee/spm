"""SCRUM-86, SCRUM-87: the Safety Officer's check, what it does to arrangements, and
SCRUM-66's notice when an event is confirmed."""
from datetime import datetime, timedelta

import pytest

from backend import arrangements, booking_rules, event_hooks, seed_users, status_rules
from backend.models import ArrangementReviewFlag, EventStatusHistory, SafetyCheck, User, VenueBooking, VenueUnavailability
from backend.security import hash_password
from backend.tests.lifecycle_world import END, START, TestSession, engine, world  # noqa: F401


def _in_review(world, title="Annual Gala"):
    """An event waiting for the check, with one Confirmed booking."""
    event = world.event("Safety Review", title=title)
    world.booking(event)
    return event


def _decide(world, event, decision, reason=None):
    return world.login("safety1").post(f"/safety/events/{event.id}/decision", json={"decision": decision, "reason": reason})


def _checks(world, event):
    world.db.expire_all()
    return world.db.query(SafetyCheck).filter_by(event_id=event.id).order_by(SafetyCheck.id).all()


# --- SCRUM-86: the role ----------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["safety_officer", "safety officer", "Safety Officer"])
def test_tc8601_safety_officer_can_sign_in_with_either_spelling(world, spelling):
    """TC-8601: both spellings are a supported role and land on /safety."""
    world.db.add(User(username="so_login", password_hash=hash_password("not-a-real-secret"), role=spelling))
    world.db.commit()
    response = world.client.post("/auth/login", json={"username": "so_login", "password": "not-a-real-secret"})
    assert response.status_code == 200
    assert response.json()["redirect_url"] == "/safety"
    assert world.client.get("/auth/me").json()["role"] == spelling


def test_tc8602_seed_adds_a_safety_officer_account_to_the_given_database(world, monkeypatch):
    """TC-8602: the local seed creates safety1 with the safety_officer role (on this test's SQLite only)."""
    monkeypatch.setenv("CONNECTSPHERE_DEV_PASSWORD", "not-a-real-secret")
    monkeypatch.setattr(seed_users, "SessionLocal", TestSession)
    monkeypatch.setattr(seed_users, "engine", engine)
    world.db.delete(world.users["safety1"])
    world.db.commit()
    seed_users.seed()
    world.db.expire_all()
    assert world.db.query(User).filter_by(username="safety1").one().role == "safety_officer"


# --- SCRUM-86: the workspace -----------------------------------------------------------


def test_tc8603_the_list_holds_only_events_in_safety_review_soonest_first(world):
    """TC-8603: other statuses stay out; each row shows the title, attendance and venue."""
    later = _in_review(world, "Later")
    soon = _in_review(world, "Soon")
    soon.proposed_start = datetime(2099, 1, 1, 10, 0)
    world.db.commit()
    for status in ["Planning", "Confirmed", "Approved", "Cancelled"]:
        world.event(status, title=status)
    rows = world.login("safety1").get("/safety/events").json()
    assert [row["title"] for row in rows] == ["Soon", "Later"]
    assert rows[1]["id"] == later.id
    assert rows[0]["expected_attendance"] == 80 and rows[0]["venues"] == ["Grand Hall"]


def test_tc8604_an_empty_list_is_an_empty_list(world):
    """TC-8604: nothing waiting is not an error."""
    world.event("Planning")
    response = world.login("safety1").get("/safety/events")
    assert (response.status_code, response.json()) == (200, [])


@pytest.mark.parametrize("user", ["organiser1", "coord1", "lead1", "venue1", "technical1", "attendee1"])
def test_tc8605_only_the_safety_officer_can_use_the_workspace(world, user):
    """TC-8605: every other role gets 403 on the list, the details and the decision, and nothing changes."""
    event = _in_review(world)
    client = world.login(user)
    assert client.get("/safety/events").status_code == 403
    assert client.get(f"/safety/events/{event.id}").status_code == 403
    assert client.post(f"/safety/events/{event.id}/decision", json={"decision": "approve"}).status_code == 403
    assert world.reload(event).status == "Safety Review"
    assert _checks(world, event) == []


def test_tc8606_signed_out_is_401(world):
    """TC-8606: no session, no workspace."""
    world.client.cookies.clear()
    assert world.client.get("/safety/events").status_code == 401


def test_tc8607_details_show_what_the_officer_needs(world):
    """TC-8607: attendance, capacity, layouts, accessibility needs, equipment with notes, and restrictions."""
    event = _in_review(world)
    world.equipment(event, "Projector", status="arranged", notes="Left of the stage")
    world.db.add(VenueUnavailability(
        venue_id=world.hall.id, start_at=END, end_at=END + timedelta(hours=1), reason="Fire door repair",
        created_by_id=world.users["venue1"].id,
    ))
    world.db.add(VenueUnavailability(
        venue_id=world.hall.id, start_at=END + timedelta(days=30), end_at=END + timedelta(days=31), reason="Far away",
        created_by_id=world.users["venue1"].id,
    ))
    world.db.commit()
    data = world.login("safety1").get(f"/safety/events/{event.id}").json()
    assert data["expected_attendance"] == 80
    assert data["accessibility_needs"] == "Two wheelchair users"
    venue = data["venue_details"][0]
    assert venue["capacity"] == 100 and venue["supported_layouts"] == ["Theatre", "Banquet"]
    assert venue["accessibility"] == "Step-free entrance" and venue["operating_hours"] == "08:00-22:00"
    assert [p["reason"] for p in venue["unavailable_periods"]] == ["Fire door repair"]
    item = data["equipment"][0]
    assert (item["equipment_type"], item["quantity"], item["technical_requirements"], item["status"]) == (
        "Projector", 2, "Left of the stage", "arranged")
    assert item["status_note"] == "Checked on site"
    assert data["checks"] == []


def test_tc8608_details_leave_out_released_bookings(world):
    """TC-8608: a booking that no longer holds the venue isn't part of the arrangements under check."""
    event = _in_review(world)
    world.booking(event, "Rejected", START + timedelta(days=1), END + timedelta(days=1))
    data = world.login("safety1").get(f"/safety/events/{event.id}").json()
    assert [v["booking_status"] for v in data["venue_details"]] == ["Confirmed"]


@pytest.mark.parametrize("status", ["Planning", "Confirmed", "Draft"])
def test_tc8609_events_not_in_safety_review_stay_hidden(world, status):
    """TC-8609: details and decisions answer 404 for an event that isn't waiting, or doesn't exist."""
    event = world.event(status)
    client = world.login("safety1")
    assert client.get(f"/safety/events/{event.id}").status_code == 404
    assert client.get("/safety/events/9999").status_code == 404
    assert client.post(f"/safety/events/{event.id}/decision", json={"decision": "approve"}).status_code == 404
    assert world.reload(event).status == status


# --- SCRUM-86: the decision ------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_tc8610_approving_needs_no_reason_and_confirms(world, reason):
    """TC-8610: approve moves the event to Confirmed, in history, and writes a check with who and when."""
    event = _in_review(world)
    response = _decide(world, event, "approve", reason)
    assert response.status_code == 200
    assert response.json()["status"] == "Confirmed"
    check = _checks(world, event)[0]
    assert (check.decision, check.reason, check.decided_by_id) == ("approve", None, world.users["safety1"].id)
    assert check.decided_at is not None
    history = world.db.query(EventStatusHistory).filter_by(event_id=event.id).one()
    assert (history.previous_status, history.new_status, history.actor_id) == (
        "Safety Review", "Confirmed", world.users["safety1"].id)


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
def test_tc8611_reject_and_request_changes_return_to_planning(world, decision):
    """TC-8611: the event goes back to Planning, and the check and history keep the reason."""
    event = _in_review(world)
    response = _decide(world, event, decision, "  Exits blocked  ")
    assert response.status_code == 200
    assert response.json()["status"] == "Planning"
    check = _checks(world, event)[0]
    assert (check.decision, check.reason) == (decision, "Exits blocked")
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id).one().reason == "Exits blocked"


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_tc8612_a_decision_that_is_not_approval_needs_a_reason(world, decision, reason):
    """TC-8612: a missing or blank reason is 422 and nothing changes."""
    event = _in_review(world)
    assert _decide(world, event, decision, reason).status_code == 422
    assert world.reload(event).status == "Safety Review"
    assert _checks(world, event) == []


def test_tc8613_an_unknown_decision_is_refused(world):
    """TC-8613: only the three decisions exist."""
    event = _in_review(world)
    assert _decide(world, event, "maybe", "Hmm").status_code == 422
    assert world.reload(event).status == "Safety Review"


def test_tc8614_a_decided_check_cannot_be_decided_again(world):
    """TC-8614: a second decision answers 404 and adds no row."""
    event = _in_review(world)
    assert _decide(world, event, "approve").status_code == 200
    assert _decide(world, event, "reject", "Too late").status_code == 404
    assert world.reload(event).status == "Confirmed"
    assert len(_checks(world, event)) == 1


def test_tc8615_every_round_is_recorded(world):
    """TC-8615: request changes, resubmit, reject, resubmit, approve leaves three checks in order."""
    event = _in_review(world)
    assert _decide(world, event, "request_changes", "Move the stage").status_code == 200
    assert world.login("coord1").post(f"/events/{event.id}/submit-safety").status_code == 200
    assert _decide(world, event, "reject", "Still blocked").status_code == 200
    assert world.login("coord1").post(f"/events/{event.id}/submit-safety").status_code == 200
    assert _decide(world, event, "approve").status_code == 200
    assert [(c.decision, c.reason) for c in _checks(world, event)] == [
        ("request_changes", "Move the stage"), ("reject", "Still blocked"), ("approve", None)]
    data = world.reload(event)
    assert data.status == "Confirmed"


def test_tc8616_earlier_checks_show_in_the_details(world):
    """TC-8616: a returned and resubmitted event shows the officer the earlier decision."""
    event = _in_review(world)
    _decide(world, event, "request_changes", "Move the stage")
    world.login("coord1").post(f"/events/{event.id}/submit-safety")
    checks = world.login("safety1").get(f"/safety/events/{event.id}").json()["checks"]
    assert [(c["decision"], c["reason"], c["decided_by"]) for c in checks] == [("request_changes", "Move the stage", "safety1")]
    assert checks[0]["decided_at"]


@pytest.mark.parametrize("user", ["coord1", "lead1", "organiser1"])
@pytest.mark.parametrize("status", ["Planning", "Approved", "Safety Review", "Submitted"])
def test_tc8617_nobody_else_reaches_confirmed(world, user, status):
    """TC-8617: the rulebook refuses Confirmed to everyone but the Safety Officer, from any status."""
    event = world.event(status)
    with pytest.raises(status_rules.StatusChangeError):
        status_rules.change_status(world.db, event, "Confirmed", world.users[user])
    world.db.rollback()
    assert world.reload(event).status == status


def test_tc8618_the_whole_way_through_over_http(world, monkeypatch):
    """TC-8618: Approved to Completed through the real routes; the history shows each step."""
    event = world.event("Approved")
    coordinator = world.login("coord1")
    assert coordinator.post(f"/events/{event.id}/start-planning").status_code == 200
    assert coordinator.post(f"/events/{event.id}/submit-safety").status_code == 409
    world.booking(event)
    assert coordinator.post(f"/events/{event.id}/submit-safety").status_code == 200
    assert _decide(world, event, "approve").status_code == 200
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END + timedelta(hours=1))
    assert world.login("coord1").post(f"/events/{event.id}/complete").status_code == 200
    world.db.expire_all()
    history = world.db.query(EventStatusHistory).filter_by(event_id=event.id).order_by(EventStatusHistory.id).all()
    assert [h.new_status for h in history] == ["Planning", "Safety Review", "Confirmed", "Completed"]


def test_tc8619_the_coordinator_is_told_each_decision_with_its_reason(world):
    """TC-8619: reject and request changes reach the Coordinator with the reason; the Organiser doesn't get those."""
    event = _in_review(world)
    _decide(world, event, "request_changes", "Move the stage")
    notices = world.notices("coord1", event)
    assert [n.kind for n in notices] == ["safety_returned"]
    assert "Move the stage" in notices[0].message and "asked for changes" in notices[0].message
    assert world.notices("organiser1", event) == []
    world.login("coord1").post(f"/events/{event.id}/submit-safety")
    _decide(world, event, "reject", "Exits blocked")
    last = world.notices("coord1", event)[-1]
    assert "was rejected" in last.message and "Exits blocked" in last.message


def test_tc8620_a_refused_decision_tells_nobody(world):
    """TC-8620: a blank reason sends no notice."""
    event = _in_review(world)
    _decide(world, event, "reject", "")
    assert world.notices("coord1", event) == []


# --- SCRUM-87: arrangements marked for review ------------------------------------------


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
def test_tc8701_returning_flags_venue_and_equipment_and_cancels_nothing(world, decision):
    """TC-8701: every holding booking and every equipment request gets a safety_review flag with the reason."""
    event = _in_review(world)
    pending = world.booking(event, "Pending", START + timedelta(days=1), END + timedelta(days=1))
    rejected = world.booking(event, "Rejected", START + timedelta(days=2), END + timedelta(days=2))
    request = world.equipment(event, "Projector", status="arranged")
    assert _decide(world, event, decision, "Exits blocked").status_code == 200
    world.db.expire_all()
    flags = arrangements.active_review_flags(world.db, event)
    assert {(f.booking_id, f.equipment_request_id) for f in flags} == {
        (event.venue_bookings[0].id, None), (pending.id, None), (None, request.id)}
    assert {(f.source, f.reason, f.created_by_id) for f in flags} == {
        (arrangements.SOURCE_SAFETY_REVIEW, "Exits blocked", world.users["safety1"].id)}
    assert world.db.get(VenueBooking, event.venue_bookings[0].id).status == "Confirmed"
    assert world.db.get(VenueBooking, rejected.id).status == "Rejected"
    assert arrangements.latest_equipment_status(world.db, request) == "arranged"


def test_tc8702_approval_flags_nothing(world):
    """TC-8702: an approved event's arrangements aren't marked."""
    event = _in_review(world)
    world.equipment(event, "Projector", status="arranged")
    _decide(world, event, "approve")
    world.db.expire_all()
    assert arrangements.active_review_flags(world.db, event) == []


def test_tc8703_returning_runs_the_hook(world, monkeypatch):
    """TC-8703: the safety_returned hook gets the event in Planning, the officer and the reason; approval doesn't run it."""
    seen = []

    def fake(db, event, actor, reason):
        seen.append((event.status, actor.username, reason))

    monkeypatch.setattr(event_hooks, "_safety_returned", [fake])
    _decide(world, _in_review(world), "reject", "Exits blocked")
    assert seen == [("Planning", "safety1", "Exits blocked")]
    _decide(world, _in_review(world, "Other"), "approve")
    assert len(seen) == 1


def test_tc8704_a_return_is_all_or_nothing(world, monkeypatch):
    """TC-8704: a failing hook leaves the status, check, flags and notice unsaved."""
    def broken(db, event, actor, reason):
        raise RuntimeError("boom")

    monkeypatch.setattr(event_hooks, "_safety_returned", [broken])
    event = _in_review(world)
    with pytest.raises(RuntimeError):
        _decide(world, event, "reject", "Exits blocked")
    assert world.reload(event).status == "Safety Review"
    assert _checks(world, event) == []
    assert arrangements.active_review_flags(world.db, event) == []
    assert world.notices("coord1", event) == []


def test_tc8705_a_second_round_flags_again_and_keeps_the_first_as_history(world):
    """TC-8705: resubmitting clears the flags; another return adds new ones; both rounds remain."""
    event = _in_review(world)
    _decide(world, event, "request_changes", "Move the stage")
    world.login("coord1").post(f"/events/{event.id}/submit-safety")
    _decide(world, event, "reject", "Still blocked")
    world.db.expire_all()
    assert [f.reason for f in arrangements.active_review_flags(world.db, event)] == ["Still blocked"]
    assert world.db.query(ArrangementReviewFlag).filter_by(event_id=event.id).count() == 2


@pytest.mark.parametrize("user", ["organiser1", "coord1", "lead1"])
def test_tc8706_the_event_people_can_read_the_flags(world, user):
    """TC-8706: the Organiser, assigned Coordinator and Lead see what's flagged, named, with the reason."""
    event = _in_review(world)
    world.equipment(event, "Projector", status="arranged")
    _decide(world, event, "request_changes", "Exits blocked")
    flags = world.login(user).get(f"/events/{event.id}/review-flags").json()
    assert sorted((f["kind"], f["label"]) for f in flags) == [("equipment", "Projector (quantity 2)"), ("venue", "Grand Hall")]
    assert {(f["source"], f["reason"], f["created_by"]) for f in flags} == {("safety_review", "Exits blocked", "safety1")}


@pytest.mark.parametrize("user", ["organiser2", "coord2", "venue1", "technical1", "attendee1", "safety1"])
def test_tc8707_nobody_else_can_read_or_clear_the_flags(world, user):
    """TC-8707: 403 for everyone else, and the flags stay active."""
    event = _in_review(world)
    _decide(world, event, "reject", "Exits blocked")
    client = world.login(user)
    assert client.get(f"/events/{event.id}/review-flags").status_code == 403
    assert client.post(f"/events/{event.id}/review-flags/clear").status_code == 403
    world.db.expire_all()
    assert len(arrangements.active_review_flags(world.db, event)) == 1


@pytest.mark.parametrize("user", ["coord1", "lead1"])
def test_tc8708_coordinator_or_lead_clears_and_the_rows_stay(world, user):
    """TC-8708: clearing empties the active list, records who, and keeps the rows."""
    event = _in_review(world)
    _decide(world, event, "reject", "Exits blocked")
    client = world.login(user)
    response = client.post(f"/events/{event.id}/review-flags/clear")
    assert (response.status_code, response.json()["cleared"]) == (200, 1)
    assert client.get(f"/events/{event.id}/review-flags").json() == []
    world.db.expire_all()
    row = world.db.query(ArrangementReviewFlag).filter_by(event_id=event.id).one()
    assert row.cleared_by_id == world.users[user].id and row.cleared_at is not None


def test_tc8709_the_organiser_can_read_but_not_clear(world):
    """TC-8709: reading is wider than clearing."""
    event = _in_review(world)
    _decide(world, event, "reject", "Exits blocked")
    assert world.login("organiser1").post(f"/events/{event.id}/review-flags/clear").status_code == 403


def test_tc8710_flags_routes_answer_404_for_an_unknown_event(world):
    """TC-8710: both routes say the event doesn't exist."""
    client = world.login("coord1")
    assert client.get("/events/9999/review-flags").status_code == 404
    assert client.post("/events/9999/review-flags/clear").status_code == 404


# --- SCRUM-66: the notice when an event is confirmed ----------------------------------


def test_tc6604_approval_tells_the_organiser_and_coordinator(world):
    """TC-6604: both get event_confirmed naming the event, through GET /notifications; nobody else does."""
    event = _in_review(world)
    _decide(world, event, "approve")
    for name in ["organiser1", "coord1"]:
        items = [n for n in world.login(name).get("/notifications").json() if n["event_id"] == event.id]
        assert [n["kind"] for n in items] == ["event_confirmed"]
        assert "Annual Gala" in items[0]["message"] and "Confirmed" in items[0]["message"]
    assert world.notices("coord2") == [] and world.notices("organiser2") == []


def test_tc6605_only_approval_sends_the_confirmed_notice(world):
    """TC-6605: a return sends no event_confirmed notice to anyone."""
    event = _in_review(world)
    _decide(world, event, "reject", "Exits blocked")
    kinds = [n.kind for name in ["organiser1", "coord1"] for n in world.notices(name, event)]
    assert "event_confirmed" not in kinds
