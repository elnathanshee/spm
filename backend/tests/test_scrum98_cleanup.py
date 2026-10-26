"""SCRUM-98: clean-up of the shadowed reassign route and the legacy coordinator page.

Done when (Jira):
  1. Only one POST /events/{event_id}/reassign handler remains, and SCRUM-23's
     and the Lead's tests still pass against it.
  2. The legacy GET /coordinator/events page is removed, or documented as kept
     and why. PO decision (26 Oct): kept, as SCRUM-24's Sprint 1 evidence, and
     marked deprecated.
  3. The test register is updated for any test that is removed or moved.

Both tests read the app's own route table and OpenAPI schema, so they need no
database rows and no login.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.main import app


def mounted_routes(routes):
    """Every route the app serves, including those inside included routers.

    FastAPI keeps each include_router() call as one entry in app.routes, with
    the router's own routes under original_router. main.py adds no prefix
    when it includes a router, so each route's path is the full path.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from mounted_routes(inner.routes)
        else:
            yield route


def test_tc9801_only_the_lead_reassign_handler_is_registered():
    """TC-9801 (Done-when 1, Cross-Cutting): the app registers exactly one
    POST /events/{event_id}/reassign, and it is lead.py's handler, the one
    SCRUM-23's and the Lead's tests already run against."""
    handlers = [
        route.endpoint
        for route in mounted_routes(app.routes)
        if getattr(route, "path", None) == "/events/{event_id}/reassign"
        and "POST" in getattr(route, "methods", set())
    ]

    assert len(handlers) == 1
    assert handlers[0].__module__ == "backend.lead"


def test_tc9802_legacy_coordinator_page_is_marked_deprecated():
    """TC-9802 (Done-when 2, Cross-Cutting): the kept HTML page is marked
    deprecated in the OpenAPI schema, and the two JSON endpoints the frontend
    uses are not."""
    paths = app.openapi()["paths"]

    assert paths["/coordinator/events"]["get"].get("deprecated") is True
    assert not paths["/coordinator/events/data"]["get"].get("deprecated", False)
    assert not paths["/coordinator/events/all-data"]["get"].get("deprecated", False)
