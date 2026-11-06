"""Sprint 5 equipment: SCRUM-41, 42, 43, 44, 40 and the equipment half of SCRUM-65.

The expected numbers come from the PO's formula: available = total - overlapping
active reservations - overlapping unavailable units, floored at 0.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, User
from backend.security import hash_password

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MAX_QUANTITY = 2147483647


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def users(db):
    """One account per role. Session token for each: tok_<username>."""
    specs = [
        ("tech", "technical support"),
        ("tech2", "technical_support"),
        ("coord", "coordinator"),
        ("coord_other", "coordinator"),
        ("lead", "lead"),
        ("org", "organiser"),
        ("venue", "venue_staff"),
        ("attendee", "attendee"),
    ]
    created = {}
    for username, role in specs:
        user = User(username=username, password_hash=hash_password("test-only"), role=role)
        db.add(user)
        db.commit()
        db.add(LoginSession(user_id=user.id, token=f"tok_{username}", valid=True))
        db.commit()
        created[username] = user
    return created


def login(client, username):
    client.cookies.clear()
    if username:
        client.cookies.set("connectsphere_session", f"tok_{username}")


def add_item(client, username="tech", **overrides):
    login(client, username)
    body = {"equipment_type": "Audio", "name": "Wireless microphone", "total_quantity": 10, "notes": "Spare batteries"}
    body.update(overrides)
    return client.post("/equipment/items", json=body)


NOT_TECH = ["coord", "lead", "org", "venue", "attendee"]


# ---------------------------------------------------------------------------
# SCRUM-41: the catalogue
# ---------------------------------------------------------------------------


def test_tc4101_technical_support_adds_an_item_and_sees_it_in_the_catalogue(client, users):
    response = add_item(client)
    assert response.status_code == 201
    item = response.json()["item"]
    assert item["equipment_type"] == "Audio"
    assert item["name"] == "Wireless microphone"
    assert item["total_quantity"] == 10
    assert item["notes"] == "Spare batteries"

    # auth.py accepts both spellings of the role, so both work here.
    login(client, "tech2")
    listed = client.get("/equipment/items")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [item["id"]]


@pytest.mark.parametrize("username", NOT_TECH)
def test_tc4102_only_technical_support_can_read_or_add_to_the_catalogue(client, users, username):
    assert add_item(client, username).status_code == 403
    login(client, username)
    assert client.get("/equipment/items").status_code == 403


def test_tc4103_signed_out_is_refused(client, users):
    login(client, None)
    assert client.get("/equipment/items").status_code == 401
    assert client.post("/equipment/items", json={}).status_code == 401


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "many", None, MAX_QUANTITY + 1, 10**30])
def test_tc4104_total_quantity_outside_the_limits_is_a_422(client, users, quantity):
    assert add_item(client, total_quantity=quantity).status_code == 422
    login(client, "tech")
    assert client.get("/equipment/items").json()["items"] == []


@pytest.mark.parametrize("quantity", [1, MAX_QUANTITY])
def test_tc4105_total_quantity_at_the_limits_is_accepted(client, users, quantity):
    response = add_item(client, total_quantity=quantity)
    assert response.status_code == 201
    assert response.json()["item"]["total_quantity"] == quantity


@pytest.mark.parametrize(
    "field,value",
    [
        ("equipment_type", ""),
        ("equipment_type", "   "),
        ("equipment_type", "x" * 101),
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 256),
    ],
)
def test_tc4106_type_and_name_must_be_present_and_fit_their_columns(client, users, field, value):
    assert add_item(client, **{field: value}).status_code == 422


def test_tc4107_the_same_type_and_name_cannot_be_added_twice_whatever_the_case(client, users):
    assert add_item(client).status_code == 201
    duplicate = add_item(client, equipment_type=" audio ", name="WIRELESS MICROPHONE")
    assert duplicate.status_code == 409
    assert "already" in duplicate.json()["detail"]
    assert add_item(client, equipment_type="Backup", name="Wireless microphone").status_code == 201


def test_tc4108_notes_are_optional_and_blank_notes_are_not_recorded(client, users):
    assert add_item(client, name="One", notes="   ").json()["item"]["notes"] is None
    body = {"equipment_type": "Audio", "name": "Two", "total_quantity": 1}
    login(client, "tech")
    assert client.post("/equipment/items", json=body).json()["item"]["notes"] is None
