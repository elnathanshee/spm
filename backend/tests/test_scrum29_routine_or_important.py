"""SCRUM-29: routine edits are told apart from important changes wherever details are edited.

SIGNIFICANT_FIELDS in events.py is the one list of important fields. PATCH stays
routine-only and sends the user to a change request. POST /amend reports which kind
of change it made. Only an important change flags arrangements.
"""

import pytest

from backend import events
from backend.arrangements import SOURCE_IMPORTANT_CHANGE, active_review_flags
from backend.change_requests import PERMITTED_FIELDS
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
