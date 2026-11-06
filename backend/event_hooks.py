"""[Sprint 5] What other features do when something happens to an event.

Cancelling an event must release its equipment reservations (SCRUM-44) and tell
its registered Attendees (SCRUM-52). An approved important change must flag
arrangements (SCRUM-50) and tell Attendees too. Those features live in their own
modules, so the story that causes the change doesn't need to know about them: it
runs the hook, and each feature registers what it does when the module loads.

Handlers run inside the caller's transaction and must not commit. The caller
commits, so the change and everything it causes are saved together or not at all.
"""

_cancelled = []
_important_change = []
_safety_returned = []
_details_changed = []


def on_event_cancelled(handler):
    """Register handler(db, event, actor, reason) to run when an event is cancelled."""
    _cancelled.append(handler)
    return handler


def on_important_change(handler):
    """Register handler(db, event, actor, changed_fields, reason) to run after an approved important change."""
    _important_change.append(handler)
    return handler


def on_safety_returned(handler):
    """Register handler(db, event, actor, reason) to run when a safety check sends an event back to Planning."""
    _safety_returned.append(handler)
    return handler


def on_details_changed(handler):
    """Register handler(db, event, actor, changed_fields) to run after a routine edit of an event's details."""
    _details_changed.append(handler)
    return handler


def event_cancelled(db, event, actor, reason):
    for handler in _cancelled:
        handler(db, event, actor, reason)


def important_change(db, event, actor, changed_fields, reason):
    for handler in _important_change:
        handler(db, event, actor, changed_fields, reason)


def safety_returned(db, event, actor, reason):
    for handler in _safety_returned:
        handler(db, event, actor, reason)


def details_changed(db, event, actor, changed_fields):
    for handler in _details_changed:
        handler(db, event, actor, changed_fields)
