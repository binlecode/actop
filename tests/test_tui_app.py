import asyncio

import pytest
from textual.widgets import Input, Static

from agtop import __version__
from agtop.agtop import build_parser
from agtop.tui.app import AgtopApp, HelpScreen

pytestmark = pytest.mark.local


def test_opening_banner_and_header_show_version():
    # The opening splash banner and the persistent header sub-title must both
    # surface the running version (regression: banner showed no version). Read
    # them off a mounted app through the rendered splash widget and public
    # sub_title, not the builder internals.
    async def _run():
        app = AgtopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            splash = str(app.query_one("#loading-splash", Static).render())
            return splash, app.sub_title or ""

    splash, sub_title = asyncio.run(_run())
    assert __version__ in splash
    assert __version__ in sub_title


def test_status_bar_exposes_only_supported_actions():
    keys = {(b[0] if isinstance(b, tuple) else b.key) for b in AgtopApp.BINDINGS}

    # Kept utilities, including the help overlay.
    assert {"q", "p", "s", "g", "t", "/", "question_mark"} <= keys

    # Removed utilities: layout toggle and dashboard-collapse no longer exist.
    assert "v" not in keys
    assert "space" not in keys

    # The framework command palette is disabled (no ^p in the status bar).
    assert AgtopApp.ENABLE_COMMAND_PALETTE is False


def test_help_overlay_documents_keys_metrics_and_alert_tokens():
    # Open the real help overlay (via the action the "?" binding is wired to)
    # and read its rendered body, so the in-app docs are validated through the
    # real screen the user sees, not a module constant.
    async def _run():
        app = AgtopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_help()
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            return str(app.screen.query_one("#help-body", Static).render())

    help_text = asyncio.run(_run())

    # Every keybinding action is described in the overlay.
    for action in ("Quit", "Pause", "Filter", "help"):
        assert action in help_text

    # The previously-undocumented alert tokens are now explained in-app.
    for token in ("THERMAL", "BW>", "PKG>", "SWAP+"):
        assert token in help_text

    # The new cur/avg/max chart context is explained too.
    assert "avg" in help_text
    assert "max" in help_text

    # The chart time-window token and color-degradation behavior are documented.
    assert "span" in help_text
    assert "NO_COLOR" in help_text


def test_escape_cancels_filter_edit_and_hides_input():
    # Esc must cancel an in-progress filter edit: discard the typed text and hide
    # the field. Drive the real key path through the mounted app and assert public
    # widget state only (no private attributes).
    async def _run():
        app = AgtopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_filter()  # open filter
            await pilot.pause()
            inp = app.query_one("#filter-input", Input)
            assert inp.display is True
            inp.focus()
            for ch in "brave":  # type a regex
                await pilot.press(ch)
            await pilot.pause()
            assert inp.value == "brave"
            await pilot.press("escape")  # cancel via the real key binding
            await pilot.pause()
            return inp.display, inp.value

    display, value = asyncio.run(_run())
    assert display is False  # field hidden
    assert value == ""  # typed text discarded (reverted)
