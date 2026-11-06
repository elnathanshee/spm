"""SCRUM-102: the event lifecycle after approval, and cancelling (with SCRUM-66's notices).

Approved -> Planning -> Safety Review -> Confirmed -> Completed. An event can be
Cancelled with a reason at any stage before Completed. The rulebook table below is
written out by hand from the PO's words, so it checks status_rules.py rather than
repeating it.
"""
from datetime import timedelta

import pytest

from backend import arrangements, booking_rules, event_hooks, lifecycle, status_rules
from backend.models import ArrangementReviewFlag, EquipmentRequest, Event, EventStatusHistory, User, VenueBooking, VenueHold
from backend.tests.lifecycle_world import END, START, world  # noqa: F401
from backend.venue_holds import run_hold_checks

ALL_STATUSES = ["Draft", "Submitted", "Under Review", "Returned", "Approved", "Rejected", "Planning",
                "Safety Review", "Confirmed", "Completed", "Cancelled"]
ACTORS = ["organiser", "coordinator", "lead", "safety_officer", "venue_staff", "technical support", "attendee"]
EARLY = ["Draft", "Submitted", "Under Review", "Returned"]
LATE = ["Approved", "Planning", "Safety Review", "Confirmed"]

LEGAL = {
    ("Draft", "Submitted"): {"organiser"},
    ("Returned", "Submitted"): {"organiser"},
    ("Submitted", "Under Review"): {"coordinator"},
    ("Submitted", "Approved"): {"coordinator"},
    ("Submitted", "Rejected"): {"coordinator"},
    ("Submitted", "Returned"): {"coordinator"},
    ("Under Review", "Approved"): {"coordinator"},
    ("Under Review", "Rejected"): {"coordinator"},
    ("Under Review", "Returned"): {"coordinator"},
    ("Approved", "Planning"): {"coordinator"},
    ("Planning", "Safety Review"): {"coordinator"},
    ("Confirmed", "Completed"): {"coordinator"},
    ("Safety Review", "Confirmed"): {"safety_officer"},
    ("Safety Review", "Planning"): {"safety_officer"},
}
for stage in EARLY:
    LEGAL[(stage, "Cancelled")] = {"organiser", "coordinator", "lead"}
for stage in LATE:
    LEGAL[(stage, "Cancelled")] = {"coordinator", "lead"}


def _actor(role):
    return User(id=1, username=role, password_hash="x", role=role)


def _event(status, organiser_id=1, coordinator_id=1):
    return Event(id=1, title="T", status=status, organiser_id=organiser_id, assigned_coordinator_id=coordinator_id)


# --- The rulebook: every allowed and every refused move -------------------------------


def test_tc10201_every_allowed_move_is_allowed_for_its_roles():
    """TC-10201: each move in the PO's table passes for each role it names."""
    for (frm, to), roles in LEGAL.items():
        for role in roles:
            status_rules.check_transition(_event(frm), to, _actor(role))


def test_tc10202_every_other_move_and_role_is_refused():
    """TC-10202: every other from/to/role combination (847 are checked) raises a status error."""
    checked = 0
    for frm in ALL_STATUSES:
        for to in ALL_STATUSES:
            for role in ACTORS:
                checked += 1
                if role in LEGAL.get((frm, to), set()):
                    continue
                with pytest.raises(status_rules.StatusChangeError):
                    status_rules.check_transition(_event(frm), to, _actor(role))
    assert checked == 11 * 11 * 7


def test_tc10203_only_the_safety_officer_from_safety_review_reaches_confirmed():
    """TC-10203: the rulebook holds exactly one route into Confirmed."""
    into_confirmed = [pair for pair in status_rules.TRANSITIONS if pair[1] == "Confirmed"]
    assert into_confirmed == [("Safety Review", "Confirmed")]
    assert status_rules.TRANSITIONS[("Safety Review", "Confirmed")] == status_rules.SAFETY_OFFICER


def test_tc10204_ownership_is_checked_as_well_as_role():
    """TC-10204: a different Coordinator or Organiser is refused; the Lead needs no assignment."""
    for frm, to in [("Approved", "Planning"), ("Planning", "Safety Review"), ("Confirmed", "Completed"), ("Planning", "Cancelled")]:
        with pytest.raises(status_rules.NotPermitted):
            status_rules.check_transition(_event(frm, coordinator_id=99), to, _actor("coordinator"))
    with pytest.raises(status_rules.NotPermitted):
        status_rules.check_transition(_event("Draft", organiser_id=99), "Cancelled", _actor("organiser"))
    status_rules.check_transition(_event("Confirmed", coordinator_id=99), "Cancelled", _actor("lead"))


def test_tc10205_both_spellings_of_the_safety_officer_role_work():
    """TC-10205: 'safety officer' and 'Safety_Officer' count as the same role, like other aliases."""
    for spelling in ["safety officer", "Safety_Officer", " safety_officer "]:
        status_rules.check_transition(_event("Safety Review"), "Confirmed", _actor(spelling))


def test_tc10206_change_status_records_who_and_why(world):
    """TC-10206: a new move writes a history row with the actor and reason."""
    event = world.event("Planning")
    status_rules.change_status(world.db, event, "Cancelled", world.users["coord1"], reason="Budget cut")
    world.db.commit()
    row = world.db.query(EventStatusHistory).filter_by(event_id=event.id).one()
    assert (row.previous_status, row.new_status, row.actor_id, row.reason) == (
        "Planning", "Cancelled", world.users["coord1"].id, "Budget cut")


# --- POST /events/{id}/start-planning --------------------------------------------------


def test_tc10207_coordinator_starts_planning(world):
    """TC-10207: the assigned Coordinator moves an Approved event to Planning, recorded in history."""
    event = world.event("Approved")
    response = world.login("coord1").post(f"/events/{event.id}/start-planning")
    assert response.status_code == 200
    assert response.json()["status"] == "Planning"
    row = world.db.query(EventStatusHistory).filter_by(event_id=event.id).one()
    assert (row.previous_status, row.new_status, row.actor_id) == ("Approved", "Planning", world.users["coord1"].id)


@pytest.mark.parametrize("user, status, code", [
    ("coord1", "Submitted", 400),
    ("coord1", "Planning", 400),
    ("coord2", "Approved", 403),
    ("organiser1", "Approved", 403),
    ("lead1", "Approved", 403),
    ("safety1", "Approved", 403),
])
def test_tc10208_start_planning_refusals(world, user, status, code):
    """TC-10208: wrong status (400) or wrong person (403) changes nothing."""
    event = world.event(status)
    assert world.login(user).post(f"/events/{event.id}/start-planning").status_code == code
    assert world.reload(event).status == status
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id).count() == 0


def test_tc10209_lifecycle_routes_answer_404_for_an_unknown_event(world):
    """TC-10209: every lifecycle route says the event doesn't exist."""
    client = world.login("coord1")
    for path, body in [("start-planning", None), ("submit-safety", None), ("complete", None), ("cancel", {"reason": "x"})]:
        assert client.post(f"/events/9999/{path}", json=body).status_code == 404


# --- POST /events/{id}/submit-safety ---------------------------------------------------


def test_tc10210_submit_with_a_confirmed_booking_and_no_equipment(world):
    """TC-10210: no equipment requests counts as arranged."""
    event = world.event("Planning")
    world.booking(event)
    response = world.login("coord1").post(f"/events/{event.id}/submit-safety")
    assert response.status_code == 200
    assert response.json()["status"] == "Safety Review"


def test_tc10211_submit_with_all_equipment_arranged(world):
    """TC-10211: the latest update decides, so a request that became arranged passes."""
    event = world.event("Planning")
    world.booking(event)
    request = world.equipment(event, status="reserved")
    arrangements.record_equipment_status(world.db, request, "arranged", None, world.users["technical1"])
    world.db.commit()
    assert world.login("coord1").post(f"/events/{event.id}/submit-safety").status_code == 200
    assert world.reload(event).status == "Safety Review"


@pytest.mark.parametrize("booking_status", [None, "Pending", "Rejected", "Expired", "Cancelled"])
def test_tc10212_submit_refused_without_a_confirmed_booking(world, booking_status):
    """TC-10212: only a Confirmed booking counts; a hold isn't enough. The refusal says what's missing."""
    event = world.event("Planning")
    if booking_status:
        world.booking(event, status=booking_status)
    response = world.login("coord1").post(f"/events/{event.id}/submit-safety")
    assert response.status_code == 409
    assert "Confirmed venue booking" in response.json()["detail"]
    assert "quipment" not in response.json()["detail"]
    assert world.reload(event).status == "Planning"
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id).count() == 0


@pytest.mark.parametrize("equipment_status", [None, "requested", "reserved", "unavailable"])
def test_tc10213_submit_refused_while_equipment_is_not_arranged(world, equipment_status):
    """TC-10213: one unarranged request blocks it, and the refusal names that request."""
    event = world.event("Planning")
    world.booking(event)
    world.equipment(event, "Projector", status="arranged")
    world.equipment(event, "Microphone", status=equipment_status)
    response = world.login("coord1").post(f"/events/{event.id}/submit-safety")
    assert response.status_code == 409
    assert "Microphone" in response.json()["detail"]
    assert "Projector" not in response.json()["detail"]
    assert world.reload(event).status == "Planning"


def test_tc10214_refusal_names_everything_missing(world):
    """TC-10214: with both gates unmet, the sentence covers the booking and the equipment."""
    event = world.event("Planning")
    world.equipment(event, "Projector")
    detail = world.login("coord1").post(f"/events/{event.id}/submit-safety").json()["detail"]
    assert "Confirmed venue booking" in detail and "Projector" in detail


@pytest.mark.parametrize("user, status, code", [
    ("coord2", "Planning", 403),
    ("organiser1", "Planning", 403),
    ("lead1", "Planning", 403),
    ("safety1", "Planning", 403),
    ("coord1", "Approved", 400),
    ("coord1", "Confirmed", 400),
])
def test_tc10215_submit_checks_the_person_and_status_before_the_gate(world, user, status, code):
    """TC-10215: someone who can't make the move gets that answer, not a list of what's missing."""
    event = world.event(status)
    response = world.login(user).post(f"/events/{event.id}/submit-safety")
    assert response.status_code == code
    assert "Confirmed venue booking" not in response.json()["detail"]
    assert world.reload(event).status == status


def test_tc10216_submitting_clears_flags_and_keeps_their_rows(world):
    """TC-10216: resubmitting says the flags have been looked at: cleared by the Coordinator, rows kept."""
    event = world.event("Planning")
    world.booking(event)
    arrangements.flag_event_arrangements(world.db, event, arrangements.SOURCE_SAFETY_REVIEW, "Fix exits", world.users["safety1"])
    world.db.commit()
    assert world.login("coord1").post(f"/events/{event.id}/submit-safety").status_code == 200
    world.db.expire_all()
    assert arrangements.active_review_flags(world.db, event) == []
    row = world.db.query(ArrangementReviewFlag).filter_by(event_id=event.id).one()
    assert row.cleared_at is not None and row.cleared_by_id == world.users["coord1"].id


def test_tc10217_a_refused_submit_leaves_flags_active(world):
    """TC-10217: a submission the gate refuses clears nothing."""
    event = world.event("Planning")
    world.booking(event, status="Pending")
    arrangements.flag_event_arrangements(world.db, event, arrangements.SOURCE_SAFETY_REVIEW, "Fix exits", world.users["safety1"])
    world.db.commit()
    assert world.login("coord1").post(f"/events/{event.id}/submit-safety").status_code == 409
    world.db.expire_all()
    assert len(arrangements.active_review_flags(world.db, event)) == 1


# --- POST /events/{id}/complete --------------------------------------------------------


def test_tc10218_complete_after_the_end_time(world, monkeypatch):
    """TC-10218: one minute after the last booking ends, the Coordinator can complete it."""
    event = world.event("Confirmed")
    world.booking(event)
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END + timedelta(minutes=1))
    response = world.login("coord1").post(f"/events/{event.id}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "Completed"
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id, new_status="Completed").count() == 1


def test_tc10219_complete_is_refused_before_the_end_and_allowed_at_it(world, monkeypatch):
    """TC-10219: boundary: one second before the end is refused with a sentence; the exact end is allowed."""
    event = world.event("Confirmed")
    world.booking(event)
    client = world.login("coord1")
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END - timedelta(seconds=1))
    response = client.post(f"/events/{event.id}/complete")
    assert response.status_code == 409
    assert "hasn't ended" in response.json()["detail"]
    assert world.reload(event).status == "Confirmed"
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END)
    assert client.post(f"/events/{event.id}/complete").status_code == 200


def test_tc10220_complete_uses_the_last_booking_not_the_first(world, monkeypatch):
    """TC-10220: with two bookings, the end is the later one's."""
    event = world.event("Confirmed")
    world.booking(event)
    world.booking(event, start=END + timedelta(days=1), end=END + timedelta(days=2))
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END + timedelta(days=1))
    assert world.login("coord1").post(f"/events/{event.id}/complete").status_code == 409


@pytest.mark.parametrize("user, status, code", [
    ("coord1", "Planning", 400),
    ("coord1", "Safety Review", 400),
    ("coord2", "Confirmed", 403),
    ("lead1", "Confirmed", 403),
    ("organiser1", "Confirmed", 403),
    ("safety1", "Confirmed", 403),
])
def test_tc10221_complete_refusals(world, user, status, code, monkeypatch):
    """TC-10221: only the assigned Coordinator completes, and only a Confirmed event."""
    event = world.event(status)
    world.booking(event)
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END + timedelta(days=1))
    assert world.login(user).post(f"/events/{event.id}/complete").status_code == code
    assert world.reload(event).status == status


# --- POST /events/{id}/cancel ----------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, None])
def test_tc10222_cancel_needs_a_reason(world, body):
    """TC-10222: a missing or blank reason is 422 and changes nothing."""
    event = world.event("Planning")
    booking = world.booking(event)
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json=body).status_code == 422
    assert world.reload(event).status == "Planning"
    assert world.reload(booking).status == "Confirmed"


@pytest.mark.parametrize("stage", EARLY + LATE)
@pytest.mark.parametrize("user", ["coord1", "lead1"])
def test_tc10223_coordinator_and_lead_cancel_at_any_stage_before_completed(world, stage, user):
    """TC-10223: the assigned Coordinator and the Lead may cancel at all eight stages."""
    event = world.event(stage)
    response = world.login(user).post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"})
    assert response.status_code == 200
    assert response.json()["status"] == "Cancelled"
    row = world.db.query(EventStatusHistory).filter_by(event_id=event.id, new_status="Cancelled").one()
    assert (row.previous_status, row.actor_id, row.reason) == (stage, world.users[user].id, "Budget cut")


@pytest.mark.parametrize("stage, code", [(s, 200) for s in EARLY] + [(s, 403) for s in LATE])
def test_tc10224_organiser_cancels_only_before_approval(world, stage, code):
    """TC-10224: the owning Organiser may cancel in Draft, Submitted, Under Review and Returned, not later."""
    event = world.event(stage)
    assert world.login("organiser1").post(f"/events/{event.id}/cancel", json={"reason": "Not needed"}).status_code == code
    assert world.reload(event).status == ("Cancelled" if code == 200 else stage)


@pytest.mark.parametrize("user", ["organiser2", "coord2", "safety1", "venue1", "technical1", "attendee1"])
def test_tc10225_nobody_else_can_cancel(world, user):
    """TC-10225: another Organiser, another Coordinator and other roles are refused."""
    event = world.event("Submitted")
    assert world.login(user).post(f"/events/{event.id}/cancel", json={"reason": "No"}).status_code == 403
    assert world.reload(event).status == "Submitted"


@pytest.mark.parametrize("stage", ["Rejected", "Completed", "Cancelled"])
@pytest.mark.parametrize("user", ["coord1", "lead1", "organiser1"])
def test_tc10226_closed_events_cannot_be_cancelled(world, stage, user):
    """TC-10226: Rejected, Completed and Cancelled events have no cancel, for anyone."""
    event = world.event(stage)
    assert world.login(user).post(f"/events/{event.id}/cancel", json={"reason": "No"}).status_code == 400
    assert world.reload(event).status == stage


def test_tc10227_cancel_releases_blocking_bookings_and_keeps_the_rows(world):
    """TC-10227: every booking that holds the venue becomes Cancelled; closed ones keep their status; rows stay."""
    event = world.event("Confirmed")
    confirmed = world.booking(event, "Confirmed")
    approved = world.booking(event, "Approved", START + timedelta(days=1), END + timedelta(days=1))
    pending = world.booking(event, "Pending", START + timedelta(days=2), END + timedelta(days=2))
    rejected = world.booking(event, "Rejected", START + timedelta(days=3), END + timedelta(days=3))
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    assert [world.reload(b).status for b in (confirmed, approved, pending, rejected)] == [
        "Cancelled", "Cancelled", "Cancelled", "Rejected"]
    assert world.db.query(VenueBooking).filter_by(event_id=event.id).count() == 4
    assert not booking_rules.is_blocking(world.reload(confirmed))


def test_tc10228_a_cancelled_hold_keeps_its_row_and_sends_no_more_notices(world):
    """TC-10228: a released hold stays as history, and the hold checks never warn about it again."""
    event = world.event("Planning")
    hold = world.booking(event, "Pending")
    hold_row = VenueHold(booking_id=hold.id, expires_at=booking_rules.utc_now() + timedelta(hours=1), warning_minutes=0)
    world.db.add(hold_row)
    world.db.commit()
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    world.db.expire_all()
    assert world.db.query(VenueHold).filter_by(booking_id=hold.id).count() == 1
    world.db.get(VenueHold, hold_row.id).expires_at = booking_rules.utc_now() - timedelta(hours=1)
    world.db.commit()
    run_hold_checks(world.db)
    world.db.expire_all()
    assert world.db.get(VenueBooking, hold.id).status == "Cancelled"
    assert [n.kind for n in world.notices("coord1", event)] == ["event_cancelled"]


def test_tc10229_an_expired_hold_stays_expired(world):
    """TC-10229: the hold check runs first, so a hold past its expiry reads Expired, not Cancelled."""
    event = world.event("Planning")
    hold = world.booking(event, "Pending")
    world.db.add(VenueHold(booking_id=hold.id, expires_at=booking_rules.utc_now() - timedelta(hours=1)))
    world.db.commit()
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    assert world.reload(hold).status == "Expired"


def test_tc10230_a_hold_past_expiry_that_no_check_has_reached_is_still_released(world):
    """TC-10230: a hold past expiry still reads Pending until the next check; releasing it stops a later 'rebook' notice."""
    event = world.event("Planning")
    hold = world.booking(event, "Pending")
    world.db.add(VenueHold(booking_id=hold.id, expires_at=booking_rules.utc_now() - timedelta(hours=1)))
    world.db.commit()
    lifecycle.release_bookings(world.reload(event))
    world.db.commit()
    run_hold_checks(world.db)
    assert world.reload(hold).status == "Cancelled"
    assert world.notices("coord1", event) == []


def test_tc10231_cancel_runs_the_hook_in_the_transaction(world, monkeypatch):
    """TC-10231: the hook gets the event, actor and reason, after the bookings are released and before the commit."""
    seen = []

    def fake(db, event, actor, reason):
        seen.append((event.id, event.status, actor.username, reason, [b.status for b in event.venue_bookings]))

    monkeypatch.setattr(event_hooks, "_cancelled", [fake])
    event = world.event("Planning")
    world.booking(event)
    assert world.login("lead1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    assert seen == [(event.id, "Cancelled", "lead1", "Budget cut", ["Cancelled"])]


def test_tc10232_a_refused_cancel_runs_no_hook(world, monkeypatch):
    """TC-10232: refused means nothing else happened either."""
    seen = []
    monkeypatch.setattr(event_hooks, "_cancelled", [lambda *args: seen.append(args)])
    event = world.event("Completed")
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "No"}).status_code == 400
    assert seen == []


def test_tc10233_cancel_is_all_or_nothing(world, monkeypatch):
    """TC-10233: a hook that fails leaves the status, bookings, history and notices untouched."""
    def broken(db, event, actor, reason):
        raise RuntimeError("reservation release failed")

    monkeypatch.setattr(event_hooks, "_cancelled", [broken])
    event = world.event("Planning")
    booking = world.booking(event)
    with pytest.raises(RuntimeError):
        world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"})
    assert world.reload(event).status == "Planning"
    assert world.reload(booking).status == "Confirmed"
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id).count() == 0
    assert world.notices("organiser1", event) == []


def test_tc10234_safety_review_is_a_routine_edit_status(world):
    """TC-10234: a routine edit is still allowed while the safety check runs, and touches no arrangement."""
    event = world.event("Safety Review")
    booking = world.booking(event)
    response = world.login("coord1").patch(f"/events/{event.id}", json={"description": "Updated"})
    assert response.status_code == 200
    assert world.reload(event).status == "Safety Review"
    assert world.reload(booking).status == "Confirmed"


# --- SCRUM-66: notices when an event is cancelled --------------------------------------


def test_tc6601_cancel_tells_the_organiser_and_coordinator_with_the_reason(world):
    """TC-6601: both get an event_cancelled notice naming the event and reason, readable through GET /notifications."""
    event = world.event("Planning")
    assert world.login("lead1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    for name in ["organiser1", "coord1"]:
        items = [n for n in world.login(name).get("/notifications").json() if n["event_id"] == event.id]
        assert [n["kind"] for n in items] == ["event_cancelled"]
        assert "Annual Gala" in items[0]["message"] and "Budget cut" in items[0]["message"]
        assert items[0]["booking_id"] is None


def test_tc6602_cancelling_an_unassigned_draft_tells_only_the_organiser(world):
    """TC-6602: with no Coordinator yet, the cancel still succeeds and the Organiser is told."""
    event = world.event("Draft", coordinator=None)
    assert world.login("organiser1").post(f"/events/{event.id}/cancel", json={"reason": "Not needed"}).status_code == 200
    assert [n.kind for n in world.notices("organiser1", event)] == ["event_cancelled"]


def test_tc6603_nobody_else_is_told_and_a_refused_cancel_tells_nobody(world):
    """TC-6603: another Coordinator or Organiser gets nothing, and a refused cancel sends nothing."""
    event = world.event("Planning")
    assert world.login("organiser1").post(f"/events/{event.id}/cancel", json={"reason": "No"}).status_code == 403
    assert world.notices("organiser1", event) == [] and world.notices("coord1", event) == []
    assert world.login("coord1").post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    assert world.notices("coord2") == [] and world.notices("organiser2") == []


# --- Review findings ------------------------------------------------------------------


@pytest.mark.parametrize("user", ["safety1", "coord1"])
def test_tc10251_start_planning_cannot_pull_an_event_out_of_safety_review(world, user):
    """TC-10251: the only way back to Planning is a safety decision, which records a reason and a check."""
    event = world.event("Safety Review")
    world.booking(event)
    response = world.login(user).post(f"/events/{event.id}/start-planning")
    assert response.status_code == 400
    world.db.expire_all()
    assert world.db.get(Event, event.id).status == "Safety Review"
    assert world.db.query(EventStatusHistory).filter_by(event_id=event.id).count() == 0


@pytest.mark.parametrize("stage", ["Cancelled", "Completed"])
def test_tc10252_a_closed_event_cannot_take_a_venue_again(world, stage):
    """TC-10252: a booking request on a closed event gets 409 and leaves the slot free for another event."""
    closed = world.event(stage, title="Closed")
    other = world.event("Planning", title="Other")
    slot = {"booking_start": "2099-05-01T02:00:00", "booking_end": "2099-05-01T04:00:00"}
    client = world.login("coord1")
    refused = client.post(f"/venues/{world.hall.id}/bookings", json={"event_id": closed.id, **slot})
    assert refused.status_code == 409
    assert world.db.query(VenueBooking).filter_by(event_id=closed.id).count() == 0
    assert client.post(f"/venues/{world.hall.id}/bookings", json={"event_id": other.id, **slot}).status_code == 201


@pytest.mark.parametrize("stage", ["Cancelled", "Completed"])
def test_tc10253_a_closed_event_takes_no_new_equipment_lines(world, stage):
    """TC-10253: recording equipment on a closed event gets 409 and adds no line."""
    event = world.event(stage)
    response = world.login("coord1").post(f"/events/{event.id}/equipment-requests", json={"equipment_type": "Mic", "quantity": 1})
    assert response.status_code == 409
    assert world.db.query(EquipmentRequest).filter_by(event_id=event.id).count() == 0


def test_tc10254_cancelling_clears_active_review_flags_and_keeps_the_rows(world):
    """TC-10254: a cancelled event has nothing left to review, and the flag rows stay as history."""
    event = world.event("Planning")
    world.booking(event)
    arrangements.flag_event_arrangements(world.db, event, arrangements.SOURCE_SAFETY_REVIEW, "Move the stage", world.users["safety1"])
    world.db.commit()
    assert world.db.query(ArrangementReviewFlag).filter_by(event_id=event.id).count() == 1
    client = world.login("coord1")
    assert client.post(f"/events/{event.id}/cancel", json={"reason": "Budget cut"}).status_code == 200
    assert client.get(f"/events/{event.id}/review-flags").json() == []
    world.db.expire_all()
    assert world.db.query(ArrangementReviewFlag).filter_by(event_id=event.id).count() == 1
