"""SCRUM-29: routine edits are told apart from important changes wherever details are edited.

SIGNIFICANT_FIELDS in events.py is the one list of important fields. PATCH stays
routine-only and sends the user to a change request. POST /amend reports which kind
of change it made. A booking request that brings new venue requirements is an
important change too. Only an important change flags arrangements.
"""

from datetime import datetime

import pytest

from backend import events
from backend.arrangements import SOURCE_IMPORTANT_CHANGE, active_review_flags
from backend.change_requests import PERMITTED_FIELDS
from backend.models import EventStatusHistory
from backend.tests.test_scrum49_change_requests import (  # noqa: F401  (fixtures)
    NEW_START,
    client,
    db_session,
    login,
    raise_request,
    world,
)
from backend.tests.test_scrum50_change_decisions import arrangements, hook_calls  # noqa: F401  (fixtures)

IMPORTANT_VALUES = {
    "proposed_start": NEW_START,
    "expected_attendance": 150,
    "venue_requirements": "Two halls",
    "equipment_requirements": "Four microphones",
}


@pytest.fixture
def awaiting_review(db_session, world):
    """Amendment is for a request still awaiting review."""
    world["event"].status = "Under Review"
    db_session.commit()
    return world["event"]


@pytest.mark.parametrize("field,value", IMPORTANT_VALUES.items())
@pytest.mark.parametrize("name", ["org", "coord"])
def test_tc2901_patch_refuses_an_important_field_and_points_to_a_change_request(client, world, db_session, name, field, value):
    """TC-2901: the refusal names the field and says to raise a change request; nothing is saved."""
    login(client, name)
    res = client.patch(f"/events/{world['event'].id}", json={"title": "New title", field: value})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert field in detail
    assert "change request" in detail
    assert "SCRUM-29" not in detail
    db_session.refresh(world["event"])
    assert world["event"].title == "Annual Gala"


def test_tc2902_a_routine_patch_flags_nothing(client, world, db_session, arrangements, hook_calls):
    """TC-2902: a routine edit on an event with arrangements saves, flags nothing, runs the details hook."""
    login(client, "org")
    res = client.patch(f"/events/{world['event'].id}", json={"description": "New words"})
    assert res.status_code == 200
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls["important"] == []
    assert hook_calls["details"] == [("org", ["description"])]


def test_tc2903_a_routine_amendment_reports_routine_and_flags_nothing(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2903: changing routine fields reports change_type routine, even with arrangements in place."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"title": "New title", "description": "New words"})
    assert res.status_code == 200
    assert res.json()["change_type"] == "routine"
    assert res.json()["arrangements_flagged"] == 0
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls["important"] == []
    assert hook_calls["details"] == [("org", ["title", "description"])]


@pytest.mark.parametrize("field,value", IMPORTANT_VALUES.items())
def test_tc2904_an_important_amendment_flags_arrangements_and_runs_the_hook(client, world, db_session, arrangements, hook_calls, awaiting_review, field, value):
    """TC-2904: each important field reports important, flags booking and equipment, runs the hook, deletes nothing."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={field: value})
    assert res.status_code == 200
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 2
    assert res.json()["status"] == "Under Review"
    flags = active_review_flags(db_session, world["event"])
    assert len(flags) == 2
    assert {flag.source for flag in flags} == {SOURCE_IMPORTANT_CHANGE}
    assert len(hook_calls["important"]) == 1
    assert hook_calls["important"][0][:2] == ("org", [field])
    assert hook_calls["details"] == []
    db_session.refresh(arrangements["booking"])
    assert arrangements["booking"].status == "Confirmed"


def test_tc2905_an_important_amendment_with_no_arrangements_flags_nothing(client, world, db_session, hook_calls, awaiting_review):
    """TC-2905: the kind is still reported, but with nothing arranged no flag is written and the hook stays quiet."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150})
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 0
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls == {"important": [], "details": []}


def test_tc2905_a_rejected_booking_is_not_an_arrangement(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2905: a booking that no longer holds a venue and no equipment means nothing to reconsider."""
    db_session.delete(arrangements["equipment"])
    arrangements["booking"].status = "Rejected"
    db_session.commit()
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150})
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 0
    assert hook_calls["important"] == []


def test_tc2906_repeating_an_important_value_is_not_an_important_change(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2906: a field sent at its current value changes nothing, so it's routine and flags nothing."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 100})
    assert res.json()["change_type"] == "routine"
    assert res.json()["changed_fields"] == []
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls == {"important": [], "details": []}


def test_tc2907_a_routine_field_with_an_important_field_counts_as_important(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2907: a mixed amendment is important, and only the important hook runs."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"title": "New title", "venue_requirements": "Two halls"})
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 2
    assert hook_calls["important"][0][1] == ["title", "venue_requirements"]
    assert hook_calls["details"] == []


def test_tc2908_a_second_important_amendment_does_not_flag_twice(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2908: active flags aren't repeated, but the hook still hears about the new change."""
    login(client, "org")
    first = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150})
    second = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 180})
    assert first.json()["arrangements_flagged"] == 2
    assert second.json()["change_type"] == "important"
    assert second.json()["arrangements_flagged"] == 0
    assert len(active_review_flags(db_session, world["event"])) == 2
    assert len(hook_calls["important"]) == 2


def test_tc2909_one_definition_of_important_everywhere(client, world, db_session):
    """TC-2909: the list has the four fields, and a change request accepts them plus description only."""
    assert set(events.SIGNIFICANT_FIELDS) == set(IMPORTANT_VALUES)
    assert set(PERMITTED_FIELDS) == set(IMPORTANT_VALUES) | {"description"}
    assert not set(events.SIGNIFICANT_FIELDS) & set(events.ROUTINE_FIELDS)


def test_tc2910_a_draft_has_no_edit_route_and_no_arrangements_to_flag(client, world, db_session, hook_calls):
    """TC-2910: a Draft is still refused by amend, and points the Organiser to submitting it."""
    world["event"].status = "Draft"
    db_session.commit()
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150})
    assert res.status_code == 400
    assert "Draft" in res.json()["detail"]
    assert "change request" not in res.json()["detail"]
    assert hook_calls == {"important": [], "details": []}


def test_tc2905_an_event_with_only_equipment_has_arrangements(client, world, db_session, arrangements, hook_calls, awaiting_review):
    """TC-2905: one equipment request and no booking still counts: it's flagged and the hook runs."""
    db_session.delete(arrangements["booking"])
    db_session.delete(arrangements["rejected"])
    db_session.commit()
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150})
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 1
    flags = active_review_flags(db_session, world["event"])
    assert [flag.equipment_request_id for flag in flags] == [arrangements["equipment"].id]
    assert len(hook_calls["important"]) == 1


def book(client, world, arrangements, **fields):
    """The Coordinator asks for a new booking, far from the existing one."""
    login(client, "coord")
    return client.post(f"/venues/{arrangements['booking'].venue_id}/bookings", json={
        "event_id": world["event"].id,
        "booking_start": "2099-07-01T01:00:00",
        "booking_end": "2099-07-01T03:00:00",
        **fields,
    })


def history_rows(db_session, event):
    return db_session.query(EventStatusHistory).filter_by(event_id=event.id).all()


def test_tc2914_new_venue_requirements_on_a_booking_are_an_important_change(client, world, db_session, arrangements, hook_calls):
    """TC-2914: the value is saved, the history names it, the arrangements made before are flagged, the hook runs."""
    world["event"].status = "Confirmed"
    db_session.commit()
    res = book(client, world, arrangements, venue_requirements="Completely different")
    assert res.status_code == 201
    assert res.json()["arrangements_flagged"] == 2

    db_session.refresh(world["event"])
    assert world["event"].venue_requirements == "Completely different"
    assert world["event"].status == "Confirmed"
    flags = active_review_flags(db_session, world["event"])
    assert {flag.source for flag in flags} == {SOURCE_IMPORTANT_CHANGE}
    # The new booking was made for the new requirements, so it needs no second look.
    assert {flag.booking_id for flag in flags} == {arrangements["booking"].id, None}
    rows = history_rows(db_session, world["event"])
    assert len(rows) == 1
    assert (rows[0].previous_status, rows[0].new_status) == ("Confirmed", "Confirmed")
    assert rows[0].actor_id == world["coord"].id
    assert "venue_requirements" in rows[0].reason
    assert [call[:2] for call in hook_calls["important"]] == [("coord", ["venue_requirements"])]
    assert hook_calls["details"] == []


@pytest.mark.parametrize("sent", [{}, {"venue_requirements": "Main hall"}, {"venue_requirements": " Main hall "}, {"venue_requirements": ""}, {"venue_requirements": "   "}])
def test_tc2915_a_booking_that_changes_no_requirements_flags_nothing(client, world, db_session, arrangements, hook_calls, sent):
    """TC-2915: nothing sent, the same value, or blank text leaves the event alone, with no flag, history or hook."""
    res = book(client, world, arrangements, **sent)
    assert res.status_code == 201
    assert res.json()["arrangements_flagged"] == 0
    db_session.refresh(world["event"])
    assert world["event"].venue_requirements == "Main hall"
    assert active_review_flags(db_session, world["event"]) == []
    assert history_rows(db_session, world["event"]) == []
    assert hook_calls == {"important": [], "details": []}


@pytest.mark.parametrize("sent,stored", [
    ("2099-03-10T14:00:00+08:00", datetime(2099, 3, 10, 14, 0)),
    ("2099-03-10T06:00:00Z", datetime(2099, 3, 10, 14, 0)),
])
def test_tc2916_the_same_moment_in_another_zone_is_not_a_change(client, world, db_session, arrangements, hook_calls, awaiting_review, sent, stored):
    """TC-2916: a zoned time for the moment the event already has reports routine and flags nothing."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"proposed_start": sent})
    assert res.json()["change_type"] == "routine"
    assert res.json()["changed_fields"] == []
    assert res.json()["arrangements_flagged"] == 0
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls == {"important": [], "details": []}
    db_session.refresh(world["event"])
    assert world["event"].proposed_start == stored


def test_tc2916_a_zoned_time_is_stored_as_singapore_wall_time(client, world, db_session, arrangements, awaiting_review):
    """TC-2916: a real change sent with a zone saves the value a change request would save."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"proposed_start": "2099-05-01T02:00:00Z"})
    assert res.json()["change_type"] == "important"
    db_session.refresh(world["event"])
    assert world["event"].proposed_start == datetime(2099, 5, 1, 10, 0)


@pytest.mark.parametrize("stored", [None, ""])
@pytest.mark.parametrize("sent", ["", " ", "   "])
def test_tc2917_blank_text_on_an_empty_field_is_not_a_change(client, world, db_session, arrangements, hook_calls, awaiting_review, stored, sent):
    """TC-2917: blank text sent to an empty field, as a stray space in the form would, reports routine and flags nothing."""
    world["event"].venue_requirements = stored
    db_session.commit()
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"venue_requirements": sent})
    assert res.json()["change_type"] == "routine"
    assert res.json()["changed_fields"] == []
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls == {"important": [], "details": []}


def test_tc2917_clearing_a_filled_field_is_still_a_change(client, world, db_session, arrangements, awaiting_review):
    """TC-2917: blank text on a field that has a value does change it, so it's important and flags."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"venue_requirements": "  "})
    assert res.json()["change_type"] == "important"
    assert res.json()["arrangements_flagged"] == 2
    db_session.refresh(world["event"])
    assert world["event"].venue_requirements is None
