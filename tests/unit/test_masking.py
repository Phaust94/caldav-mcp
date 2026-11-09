"""Unit tests for the CALDAV_MCP_MASK_EVENTS / CALDAV_MCP_KEEP_TITLES feature.

Covers:

1. Env-var parsing (including the unprefixed ``MASK_EVENTS`` / ``KEEP_TITLES``
   aliases and the rule that ``KEEP_TITLES`` is inert without masking)
2. :func:`caldav_mcp.masking.mask_event` semantics
3. Masking at the serialization boundary (:func:`_event_to_dict`)
4. No leakage through every read tool that returns event data
"""

import os
import sys
from contextlib import contextmanager
from datetime import datetime
from unittest import mock

import pytest
from conftest import FakeCalendar, FakeEvent, patch_caldav
from icalendar import Calendar, Event, vCalAddress, vText

from caldav_mcp.calendar import _event_to_dict
from caldav_mcp.config import KEEP_TITLES, MASK_EVENTS
from caldav_mcp.masking import MASKED_EVENT_TITLE, mask_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENSITIVE = {
    "summary": "Board meeting",
    "description": "Discuss layoffs and the acquisition",
    "location": "HQ Room 5",
    "categories": "confidential",
    "attendee": "alice@example.com",
}


def _build_event(
    uid="uid-1",
    summary=SENSITIVE["summary"],
    description=SENSITIVE["description"],
    location=SENSITIVE["location"],
    categories=SENSITIVE["categories"],
    attendees=(SENSITIVE["attendee"],),
):
    """Build a Calendar containing one fully populated VEVENT."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", datetime(2026, 8, 18, 9, 0, 0))
    ev.add("dtend", datetime(2026, 8, 18, 10, 0, 0))
    ev.add("location", location)
    ev.add("description", description)
    ev.add("categories", categories)
    for email in attendees:
        attendee = vCalAddress("mailto:" + email)
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        ev.add("attendee", attendee, encode=False)
    cal.add_component(ev)
    return cal


def _event_dict(**kwargs):
    return _event_to_dict(FakeEvent(_build_event(**kwargs)))


@contextmanager
def _masking(mask: bool, keep: bool):
    """Temporarily set the masking flags read by ``mask_event``."""
    with (
        mock.patch("caldav_mcp.masking.MASK_EVENTS", mask),
        mock.patch("caldav_mcp.masking.KEEP_TITLES", keep),
    ):
        yield


def _reload_config_flags(env: dict[str, str]) -> tuple[bool, bool]:
    """Re-import ``caldav_mcp.config`` with a controlled environment.

    Returns ``(MASK_EVENTS, KEEP_TITLES)``.  All ``caldav_mcp`` modules are
    removed and restored so no cross-test pollution occurs.
    """
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    saved = {k: sys.modules[k] for k in to_remove}
    for k in to_remove:
        del sys.modules[k]

    irrelevant = {"CALDAV_MCP_MASK_EVENTS", "CALDAV_MCP_KEEP_TITLES", "MASK_EVENTS", "KEEP_TITLES"}
    patched = {k: v for k, v in os.environ.items() if k not in irrelevant}
    patched.update(env)

    try:
        with mock.patch.dict(os.environ, patched, clear=True):
            from caldav_mcp import config as cfg  # noqa: E402

            return cfg.MASK_EVENTS, cfg.KEEP_TITLES
    finally:
        for k in [k for k in sys.modules if k.startswith("caldav_mcp")]:
            del sys.modules[k]
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# 1. Env-var parsing
# ---------------------------------------------------------------------------


def test_masking_disabled_by_default():
    assert _reload_config_flags({}) == (False, False)


@pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes"])
def test_mask_events_trueish(val: str):
    assert _reload_config_flags({"CALDAV_MCP_MASK_EVENTS": val}) == (True, False)


@pytest.mark.parametrize("val", ["false", "False", "0", "no", "", "garbage"])
def test_mask_events_falseish(val: str):
    assert _reload_config_flags({"CALDAV_MCP_MASK_EVENTS": val}) == (False, False)


def test_keep_titles_trueish():
    env = {"CALDAV_MCP_MASK_EVENTS": "true", "CALDAV_MCP_KEEP_TITLES": "true"}
    assert _reload_config_flags(env) == (True, True)


def test_unprefixed_aliases_are_accepted():
    env = {"MASK_EVENTS": "true", "KEEP_TITLES": "true"}
    assert _reload_config_flags(env) == (True, True)


def test_prefixed_vars_win_over_aliases():
    env = {
        "CALDAV_MCP_MASK_EVENTS": "false",
        "MASK_EVENTS": "true",
        "CALDAV_MCP_KEEP_TITLES": "false",
        "KEEP_TITLES": "true",
    }
    assert _reload_config_flags(env) == (False, False)


def test_keep_titles_alone_does_not_enable_masking():
    assert _reload_config_flags({"CALDAV_MCP_KEEP_TITLES": "true"}) == (False, True)


# ---------------------------------------------------------------------------
# 2. mask_event semantics
# ---------------------------------------------------------------------------


def test_mask_event_disabled_returns_input_unchanged():
    event = {"uid": "u", "summary": "Secret"}
    with _masking(mask=False, keep=False):
        assert mask_event(event) is event


def test_mask_event_blanks_details_and_replaces_title():
    original = {
        "uid": "u1",
        "summary": "Secret",
        "dtstart": "2026-08-18T09:00:00",
        "dtend": "2026-08-18T10:00:00",
        "location": "HQ",
        "description": "secret",
        "categories": "private",
        "attendees": "mailto:a@example.com",
    }
    with _masking(mask=True, keep=False):
        masked = mask_event(original)

    assert masked == {
        "uid": "u1",
        "summary": MASKED_EVENT_TITLE,
        "dtstart": "2026-08-18T09:00:00",
        "dtend": "2026-08-18T10:00:00",
        "location": "",
        "description": "",
        "categories": "",
        "attendees": "",
    }
    # The input dict must not be mutated.
    assert original["summary"] == "Secret"


def test_mask_event_keeps_titles_when_enabled():
    original = {
        "uid": "u1",
        "summary": "Team standup",
        "dtstart": "2026-08-18T09:00:00",
        "dtend": "",
        "location": "HQ",
        "description": "secret",
        "categories": "",
        "attendees": "mailto:a@example.com",
    }
    with _masking(mask=True, keep=True):
        masked = mask_event(original)

    assert masked["summary"] == "Team standup"
    assert masked["location"] == ""
    assert masked["description"] == ""
    assert masked["attendees"] == ""


def test_dropped_keys_are_not_copied():
    """Any detail field beyond the allowlist is dropped, not forwarded."""
    original = {"uid": "u", "summary": "s", "organizer": "mailto:boss@example.com"}
    with _masking(mask=True, keep=False):
        masked = mask_event(original)
    assert "organizer" not in masked


# ---------------------------------------------------------------------------
# 3. Serialization boundary
# ---------------------------------------------------------------------------


def test_event_to_dict_masked_by_default():
    with _masking(mask=True, keep=False):
        d = _event_dict()

    assert d["uid"] == "uid-1"
    assert d["summary"] == MASKED_EVENT_TITLE
    assert d["dtstart"] == "2026-08-18T09:00:00"
    assert d["dtend"] == "2026-08-18T10:00:00"
    assert d["location"] == ""
    assert d["description"] == ""
    assert d["categories"] == ""
    assert d["attendees"] == ""
    assert SENSITIVE["description"] not in d.values()
    assert SENSITIVE["attendee"] not in d.values()


def test_event_to_dict_keep_titles():
    with _masking(mask=True, keep=True):
        d = _event_dict()

    assert d["summary"] == SENSITIVE["summary"]
    assert d["dtstart"] == "2026-08-18T09:00:00"
    assert d["location"] == ""
    assert d["description"] == ""
    assert d["attendees"] == ""


def test_event_to_dict_keep_titles_inert_without_masking():
    with _masking(mask=False, keep=True):
        d = _event_dict()

    assert d["summary"] == SENSITIVE["summary"]
    assert d["location"] == SENSITIVE["location"]
    assert d["description"] == SENSITIVE["description"]
    assert SENSITIVE["attendee"] in d["attendees"]


def test_event_to_dict_masks_missing_component_fallback():
    class BareEvent:
        id = "fallback-id"

    with _masking(mask=True, keep=False):
        d = _event_to_dict(BareEvent())

    assert d["uid"] == "fallback-id"
    assert d["summary"] == MASKED_EVENT_TITLE


# ---------------------------------------------------------------------------
# 4. No leakage through read tools
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_calendar():
    fake_cal = FakeCalendar(event=FakeEvent(_build_event()))
    patchers = patch_caldav(fake_cal)
    try:
        yield fake_cal
    finally:
        for p in patchers:
            p.stop()


def test_search_cannot_match_hidden_fields(fake_calendar):
    import server
    from server import Status

    with _masking(mask=True, keep=False):
        result = server.caldav_search_events(query="layoffs")

    assert result.status == Status.EMPTY
    # The message only echoes the caller-supplied query, never event data.
    for value in SENSITIVE.values():
        assert value not in result.message


def test_search_matches_masked_title_without_leaking(fake_calendar):
    import server
    from server import Status

    with _masking(mask=True, keep=False):
        result = server.caldav_search_events(query=MASKED_EVENT_TITLE)

    assert result.status == Status.OK
    assert result.data[0]["summary"] == MASKED_EVENT_TITLE
    for value in SENSITIVE.values():
        assert value not in result.message


def test_get_event_by_uid_hides_details(fake_calendar):
    import server
    from server import Status

    with _masking(mask=True, keep=False):
        result = server.caldav_get_event_by_uid(uid="uid-1")

    assert result.status == Status.OK
    assert "Summary: masked_event" in result.message
    for value in SENSITIVE.values():
        assert value not in result.message


def test_list_attendees_hidden_when_masked(fake_calendar):
    import server
    from server import Status

    with _masking(mask=True, keep=False):
        result = server.caldav_list_attendees(uid="uid-1")

    assert result.status == Status.EMPTY
    assert SENSITIVE["attendee"] not in result.message


def test_get_events_and_freebusy_only_expose_times(fake_calendar):
    import server
    from server import Status

    with _masking(mask=True, keep=False):
        events = server.caldav_get_events(start="2026-08-18", end="2026-08-19")
        freebusy = server.caldav_get_freebusy(start="2026-08-18", end="2026-08-19")

    assert events.status == Status.OK
    assert events.data[0]["summary"] == MASKED_EVENT_TITLE
    assert events.data[0]["dtstart"] == "2026-08-18T09:00:00"

    assert freebusy.status == Status.OK
    assert freebusy.data[0]["summary"] == MASKED_EVENT_TITLE
    for value in SENSITIVE.values():
        assert value not in events.message
        assert value not in freebusy.message


def test_imported_flags_are_booleans():
    assert isinstance(MASK_EVENTS, bool)
    assert isinstance(KEEP_TITLES, bool)
