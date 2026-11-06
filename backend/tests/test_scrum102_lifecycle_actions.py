"""SCRUM-102: what the event detail dialog offers. The dialog must show only what the
signed-in role may do and say why a button is unavailable. Equipment status and the
end time are the backend's to judge, so the backend answers.
"""
from datetime import timedelta

import pytest

from backend import booking_rules
from backend.tests.lifecycle_world import END, world  # noqa: F401


def _actions(world, user, event):
    response = world.login(user).get(f"/events/{event.id}/lifecycle-actions")
    assert response.status_code == 200
    return {a["action"]: a for a in response.json()}


def test_tc10235_each_stage_offers_the_assigned_coordinator_its_next_step(world):
    """TC-10235: Approved offers start-planning; Planning offers submit-safety; Safety Review offers only cancel."""
    assert sorted(_actions(world, "coord1", world.event("Approved"))) == ["cancel", "start-planning"]
    assert sorted(_actions(world, "coord1", world.event("Planning"))) == ["cancel", "submit-safety"]
    assert sorted(_actions(world, "coord1", world.event("Safety Review"))) == ["cancel"]


def test_tc10236_submit_is_offered_with_the_reason_it_is_unavailable(world):
    """TC-10236: while the gate isn't met the button is unavailable and says what's missing; once met it's available."""
    event = world.event("Planning")
    world.equipment(event, "Projector")
    action = _actions(world, "coord1", event)["submit-safety"]
    assert action["available"] is False
    assert "Confirmed venue booking" in action["why"] and "Projector" in action["why"]

    ready = world.event("Planning", title="Ready")
    world.booking(ready)
    action = _actions(world, "coord1", ready)["submit-safety"]
    assert (action["available"], action["why"]) == (True, None)


def test_tc10237_complete_is_unavailable_until_the_event_ends(world, monkeypatch):
    """TC-10237: before the end it says so; at the end it's available."""
    event = world.event("Confirmed")
    world.booking(event)
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END - timedelta(minutes=1))
    action = _actions(world, "coord1", event)["complete"]
    assert action["available"] is False and "hasn't ended" in action["why"]
    monkeypatch.setattr(booking_rules, "utc_now", lambda: END)
    assert _actions(world, "coord1", event)["complete"]["available"] is True


def test_tc10238_each_role_sees_only_its_own_actions(world):
    """TC-10238: the Lead sees only cancel, another Coordinator sees nothing, an Organiser sees cancel before approval."""
    planning = world.event("Planning")
    assert sorted(_actions(world, "lead1", planning)) == ["cancel"]
    assert _actions(world, "coord2", planning) == {}
    submitted = world.event("Submitted")
    assert _actions(world, "organiser1", submitted)["cancel"]["available"] is True


def test_tc10239_the_organiser_is_told_why_they_can_no_longer_cancel(world):
    """TC-10239: after approval the Organiser's cancel is unavailable, with the reason."""
    action = _actions(world, "organiser1", world.event("Approved"))["cancel"]
    assert action["available"] is False
    assert "Coordinator or a Lead" in action["why"]


@pytest.mark.parametrize("status", ["Rejected", "Completed", "Cancelled"])
@pytest.mark.parametrize("user", ["coord1", "lead1", "organiser1"])
def test_tc10240_closed_events_offer_nothing(world, status, user):
    """TC-10240: no action on an event whose life is over."""
    assert _actions(world, user, world.event(status)) == {}


@pytest.mark.parametrize("user", ["organiser2", "safety1", "venue1", "technical1", "attendee1"])
def test_tc10241_other_people_cannot_ask(world, user):
    """TC-10241: another Organiser and other roles get 403; an unknown event is 404."""
    event = world.event("Planning")
    client = world.login(user)
    assert client.get(f"/events/{event.id}/lifecycle-actions").status_code == 403
    assert client.get("/events/9999/lifecycle-actions").status_code == 404
