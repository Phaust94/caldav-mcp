"""Event masking for sensitive calendars.

When event masking is enabled (``CALDAV_MCP_MASK_EVENTS=true``), every event
serialized by :func:`caldav_mcp.calendar._event_to_dict` is stripped of its
details before it reaches an MCP client:

* ``summary`` becomes ``masked_event`` unless ``CALDAV_MCP_KEEP_TITLES=true``
* ``location``, ``description``, ``categories`` and ``attendees`` are emptied

Start/end times and the UID are preserved.  The UID is kept because every
write tool (update/delete/move/attendee management) addresses events by UID;
it is an opaque server-side identifier, not user-authored content.

Masking is applied at the serialization boundary, so it covers every read
tool — ``caldav_get_events``, ``caldav_get_today_events``,
``caldav_get_week_events``, ``caldav_get_event_by_uid``,
``caldav_search_events``, ``caldav_get_freebusy`` and
``caldav_list_attendees`` — in both simple and pro (fan-out) mode.

Search behaviour: because ``caldav_search_events`` matches against the
*serialized* event dict, masked fields are invisible to it.  A query can only
match ``masked_event`` (or the real titles when ``KEEP_TITLES`` is enabled),
so search cannot be used as an oracle to recover hidden details.
"""

from __future__ import annotations

from caldav_mcp.config import KEEP_TITLES, MASK_EVENTS

#: Replacement title used when masking is enabled and titles are not kept.
MASKED_EVENT_TITLE = "masked_event"


def mask_event(event: dict[str, str]) -> dict[str, str]:
    """Return a masked copy of an event dict produced by ``_event_to_dict``.

    The returned dict always has the same keys as the input.  When masking is
    disabled the input dict is returned unchanged (same object).  When it is
    enabled, only ``uid``, ``dtstart`` and ``dtend`` (plus the title, if
    ``KEEP_TITLES`` is set) carry data; every other field is an empty string.

    Building the result from an explicit allowlist — rather than mutating a
    copy — ensures any detail field added to ``_event_to_dict`` in the future
    is masked by default.
    """
    if not MASK_EVENTS:
        return event

    return {
        "uid": event.get("uid", ""),
        "summary": event.get("summary", "") if KEEP_TITLES else MASKED_EVENT_TITLE,
        "dtstart": event.get("dtstart", ""),
        "dtend": event.get("dtend", ""),
        "location": "",
        "description": "",
        "categories": "",
        "attendees": "",
    }
