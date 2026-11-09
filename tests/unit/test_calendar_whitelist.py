"""Unit tests for the calendar whitelist (``CALDAV_MCP_CALENDAR_WHITELIST``).

Covers:

1. Env-var parsing (trimming, empties, alias, default-off)
2. ``_get_calendar`` enforcement — listing, default selection, denial
3. ``_list_calendars_core`` filtering (env whitelist + pro-mode allowlist)
4. The fan-out wiring that passes configured calendars to the list handler
"""

import os
import sys
from unittest import mock

import pytest
from conftest import FakeCalendar, FakeClient

from caldav_mcp.calendar import _get_calendar
from caldav_mcp.config import _parse_calendar_whitelist
from caldav_mcp.errors import NotFoundError
from caldav_mcp.tools.queries import _list_calendars_core

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(*names):
    return FakeClient([FakeCalendar(name=n, url=f"https://dav/{n}") for n in names])


def _reload_whitelist(env: dict[str, str]) -> frozenset[str]:
    """Re-import ``caldav_mcp.config`` with a controlled environment."""
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    saved = {k: sys.modules[k] for k in to_remove}
    for k in to_remove:
        del sys.modules[k]

    irrelevant = {"CALDAV_MCP_CALENDAR_WHITELIST", "CALENDAR_WHITELIST"}
    patched = {k: v for k, v in os.environ.items() if k not in irrelevant}
    patched.update(env)

    try:
        with mock.patch.dict(os.environ, patched, clear=True):
            from caldav_mcp import config as cfg  # noqa: E402

            return cfg.CALENDAR_WHITELIST
    finally:
        for k in [k for k in sys.modules if k.startswith("caldav_mcp")]:
            del sys.modules[k]
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# 1. Parsing
# ---------------------------------------------------------------------------


def test_parse_empty_is_disabled():
    assert _parse_calendar_whitelist("") == frozenset()


def test_parse_trims_and_drops_empty_entries():
    assert _parse_calendar_whitelist(" Work , Travel ,, ") == frozenset({"Work", "Travel"})


def test_whitelist_disabled_by_default():
    assert _reload_whitelist({}) == frozenset()


def test_whitelist_parsed_from_env():
    assert _reload_whitelist({"CALDAV_MCP_CALENDAR_WHITELIST": "Work,Travel"}) == frozenset(
        {"Work", "Travel"}
    )


def test_unprefixed_alias_is_accepted():
    assert _reload_whitelist({"CALENDAR_WHITELIST": "Work"}) == frozenset({"Work"})


def test_prefixed_var_wins_over_alias():
    env = {"CALDAV_MCP_CALENDAR_WHITELIST": "Work", "CALENDAR_WHITELIST": "Private"}
    assert _reload_whitelist(env) == frozenset({"Work"})


def test_explicit_empty_prefixed_var_disables_alias():
    env = {"CALDAV_MCP_CALENDAR_WHITELIST": "", "CALENDAR_WHITELIST": "Private"}
    assert _reload_whitelist(env) == frozenset()


# ---------------------------------------------------------------------------
# 2. _get_calendar enforcement
# ---------------------------------------------------------------------------


def test_default_selection_skips_non_whitelisted_calendars():
    client = _client("Private", "Work")
    with mock.patch("caldav_mcp.calendar.CALENDAR_WHITELIST", frozenset({"Work"})):
        assert _get_calendar(client).name == "Work"


def test_explicit_selection_within_whitelist():
    client = _client("Work", "Travel")
    with mock.patch("caldav_mcp.calendar.CALENDAR_WHITELIST", frozenset({"Work", "Travel"})):
        assert _get_calendar(client, "Travel").name == "Travel"


def test_denied_calendar_raises_not_found_without_listing_it():
    client = _client("Private", "Work")
    with mock.patch("caldav_mcp.calendar.CALENDAR_WHITELIST", frozenset({"Work"})):
        with pytest.raises(NotFoundError) as exc:
            _get_calendar(client, "Private")

    message = str(exc.value)
    assert "Private" in message  # echoes the requested name
    assert "Available: Work" in message
    # The hidden calendar must not be advertised as available.
    assert "Available: Work, Private" not in message
    assert "Private, " not in message


def test_all_calendars_filtered_out_raises_not_found():
    client = _client("Private")
    with mock.patch("caldav_mcp.calendar.CALENDAR_WHITELIST", frozenset({"Work"})):
        with pytest.raises(NotFoundError, match="No calendars found"):
            _get_calendar(client)


def test_empty_whitelist_preserves_existing_behaviour():
    client = _client("Private", "Work")
    with mock.patch("caldav_mcp.calendar.CALENDAR_WHITELIST", frozenset()):
        assert _get_calendar(client).name == "Private"
        assert _get_calendar(client, "Work").name == "Work"


# ---------------------------------------------------------------------------
# 3. _list_calendars_core filtering
# ---------------------------------------------------------------------------


def test_list_filters_by_env_whitelist():
    client = _client("Private", "Work", "Travel")
    with mock.patch("caldav_mcp.tools.queries.CALENDAR_WHITELIST", frozenset({"Work", "Travel"})):
        names = [c["name"] for c in _list_calendars_core(client)]
    assert names == ["Work", "Travel"]


def test_list_filters_by_pro_mode_allowlist():
    client = _client("Private", "Work", "Travel")
    with mock.patch("caldav_mcp.tools.queries.CALENDAR_WHITELIST", frozenset()):
        names = [c["name"] for c in _list_calendars_core(client, allowed=("Work",))]
    assert names == ["Work"]


def test_list_applies_both_filters():
    client = _client("Private", "Work", "Travel")
    with mock.patch("caldav_mcp.tools.queries.CALENDAR_WHITELIST", frozenset({"Travel"})):
        names = [c["name"] for c in _list_calendars_core(client, allowed=("Work", "Travel"))]
    assert names == ["Travel"]


def test_list_unfiltered_returns_everything():
    client = _client("Private", "Work")
    with mock.patch("caldav_mcp.tools.queries.CALENDAR_WHITELIST", frozenset()):
        names = [c["name"] for c in _list_calendars_core(client)]
    assert names == ["Private", "Work"]


def test_list_handler_hides_injected_parameter():
    import inspect

    import server

    params = inspect.signature(server.caldav_list_calendars).parameters
    assert "_allowed_calendars" not in params
    assert "client" not in params


def test_list_tool_filters_in_simple_mode():
    from conftest import patch_caldav

    fake_cal = FakeCalendar(name="Work", url="https://dav/Work")
    patchers = patch_caldav(fake_cal)
    try:
        with mock.patch("caldav_mcp.tools.queries.CALENDAR_WHITELIST", frozenset({"Work"})):
            import server
            from server import Status

            result = server.caldav_list_calendars()
        assert result.status == Status.OK
        assert result.data == [{"name": "Work", "url": "https://dav/Work"}]
    finally:
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# 4. Fan-out wiring
# ---------------------------------------------------------------------------


def test_fanout_passes_configured_calendars_to_list_handler():
    from caldav_mcp.errors import ToolResult
    from caldav_mcp.fanout import RemoteScope
    from caldav_mcp.tools import _execute_fanout

    remote = mock.Mock()
    remote.name = "remote1"
    scope = RemoteScope(
        config_name="cfg",
        remote=remote,
        calendar_names=("Work", "Travel"),
    )

    captured = {}

    def fn(client, _allowed_calendars=None):
        captured["allowed"] = _allowed_calendars
        return ToolResult.success("ok", data=[])

    with mock.patch("caldav_mcp.tools._resolve_pro_client_for_scope", return_value=object()):
        _execute_fanout(
            fn,
            (scope,),
            {},
            "",
            needs_calendar=False,
            kwargs={},
            once_per_remote=True,
        )

    # The full configured set survives the once_per_remote truncation.
    assert captured["allowed"] == ("Work", "Travel")
