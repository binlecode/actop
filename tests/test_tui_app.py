import asyncio

import pytest
from textual.widgets import Input, Static

from actop import __version__
from actop.actop import build_parser
from actop.tui.app import ActopApp, HelpScreen

pytestmark = pytest.mark.local


def test_opening_banner_and_header_show_version():
    # The opening splash banner and the persistent header sub-title must both
    # surface the running version (regression: banner showed no version). Read
    # them off a mounted app through the rendered splash widget and public
    # sub_title, not the builder internals.
    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            splash = str(app.query_one("#loading-splash", Static).render())
            return splash, app.sub_title or ""

    splash, sub_title = asyncio.run(_run())
    assert __version__ in splash
    assert __version__ in sub_title


def test_status_bar_exposes_only_supported_actions():
    keys = {(b[0] if isinstance(b, tuple) else b.key) for b in ActopApp.BINDINGS}

    # Kept utilities, including the help overlay and the layout-preset cycle.
    assert {"q", "p", "s", "g", "l", "t", "/", "question_mark"} <= keys

    # Removed utilities: the old `v` view-toggle and `space` dashboard-collapse
    # no longer exist (the layout cycle now lives on `l`, asserted above).
    assert "v" not in keys
    assert "space" not in keys

    # The framework command palette is disabled (no ^p in the status bar).
    assert ActopApp.ENABLE_COMMAND_PALETTE is False


def test_pressing_l_cycles_the_dashboard_layout_preset():
    # The `l` binding must flip the dashboard's requested preset grid<->stack.
    # Drive it through the real key press on a mounted app (wide terminal so a
    # grid request is not auto-degraded, keeping requested==effective).
    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            from actop.tui.widgets import HardwareDashboard

            dash = app.query_one("#hardware-dash", HardwareDashboard)
            # The headless harness auto-focuses the (hidden) filter Input, which
            # would swallow the single-letter key; clear focus so `l` routes to
            # the app binding the way it does when the filter is inactive.
            app.set_focus(None)
            before = dash.layout_preset
            await pilot.press("l")
            await pilot.pause()
            after_one = dash.layout_preset
            await pilot.press("l")
            await pilot.pause()
            after_two = dash.layout_preset
            return before, after_one, after_two

    before, after_one, after_two = asyncio.run(_run())
    assert {before, after_one} == {"grid", "stack"}  # one press flips it
    assert after_two == before  # a second press flips back


def test_help_overlay_documents_keys_metrics_and_alert_tokens():
    # Open the real help overlay (via the action the "?" binding is wired to)
    # and read its rendered body, so the in-app docs are validated through the
    # real screen the user sees, not a module constant.
    async def _run():
        app = ActopApp(build_parser().parse_args([]))
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
    for token in ("THERMAL", "THROTTLING", "MEM-BOUND>", "PKG>", "SWAP+"):
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
        # Opening the filter requires the process table to be visible.
        app = ActopApp(build_parser().parse_args(["--show-processes"]))
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


def test_filter_unavailable_until_process_table_shown():
    # The `/` filter only applies to the process table, so its binding must be
    # hidden + inert while the table is off, and become available once `t` shows
    # the table. Drive public actions / check_action / widget state only.
    async def _run():
        app = ActopApp(build_parser().parse_args([]))  # table off by default
        async with app.run_test() as pilot:
            await pilot.pause()
            off = app.check_action("toggle_filter", ())  # hidden + inert
            app.action_toggle_filter()  # body guard: should be a no-op
            await pilot.pause()
            hidden_while_off = app.query_one("#filter-input", Input).display
            app.action_toggle_processes()  # reveal table (the `t` action)
            await pilot.pause()
            on = app.check_action("toggle_filter", ())  # now available
            return off, hidden_while_off, on

    off, hidden_while_off, on = asyncio.run(_run())
    assert off is False  # binding hidden when table off
    assert hidden_while_off is False  # `/` did not open the input
    assert on is True  # binding available once table shown
