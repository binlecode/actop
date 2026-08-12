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
    assert {"q", "p", "s", "g", "l", "t", "/", "question_mark", "space"} <= keys

    # Removed utilities: the old `v` view-toggle no longer exists
    # (the layout cycle now lives on `l`, asserted above).
    assert "v" not in keys

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


def test_uppercase_letter_keys_drive_the_same_actions():
    # Regression: with a Chinese input source selected, Caps Lock is how macOS
    # forces direct ASCII — and it delivers "L", which Textual names as a key
    # distinct from "l", so every letter action used to go dead in that mode.
    # Drive the real uppercase presses and assert each action's public state
    # moves. Three separate actions, because the bug was never per-key: the
    # whole letter set went dead at once, and a single-key check would pass
    # while most of the alias list was still missing.
    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            from actop.tui.widgets import HardwareDashboard

            dash = app.query_one("#hardware-dash", HardwareDashboard)
            app.set_focus(None)  # as in the lowercase test: unfocus the filter
            state = {}
            for key, read in (
                ("L", lambda: dash.layout_preset),
                ("C", lambda: dash.show_cores),
                ("G", lambda: dash.chart_glyph),
            ):
                before = read()
                await pilot.press(key)
                await pilot.pause()
                state[key] = (before, read())
            return state

    state = asyncio.run(_run())
    assert set(state["L"]) == {"grid", "stack"}  # uppercase L flips the layout
    assert state["C"][0] is not state["C"][1]  # uppercase C toggles core panels
    assert set(state["G"]) == {"dots", "block"}  # uppercase G cycles the glyph


def test_uppercase_q_still_quits():
    # The worst symptom of the lowercase-only bindings: a user in CJK input mode
    # could not even exit. Driven separately since it tears the app down.
    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            app.set_focus(None)
            await pilot.press("Q")
            await pilot.pause()
            return app.is_running

    assert asyncio.run(_run()) is False


def test_pressing_t_cycles_through_curated_themes():
    """Pressing t cycles the app theme through the curated cycle and wraps."""

    async def _run():
        app = ActopApp(build_parser().parse_args(["--theme", "nord"]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_focus(None)
            assert app.theme == "nord"
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "dracula"
            # Wrap from the last theme back to the first
            app.theme = "textual-light"
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "textual-dark"
            return True

    assert asyncio.run(_run())


def test_theme_defaults_to_textual_dark():
    """Without --theme, the app starts in textual-dark (current behaviour)."""

    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            return app.theme

    theme = asyncio.run(_run())
    assert theme == "textual-dark"


def test_p_toggles_process_table():
    """Pressing p toggles the process table visibility."""

    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_focus(None)
            from textual.widgets import DataTable

            table = app.query_one("#process-table", DataTable)
            assert table.display is False
            await pilot.press("p")
            await pilot.pause()
            assert table.display is True
            await pilot.press("p")
            await pilot.pause()
            assert table.display is False
            return True

    assert asyncio.run(_run())


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

    # Themes are documented in the help overlay.
    assert "Theme" in help_text
    assert "textual-dark" in help_text


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
            app.action_toggle_processes()  # reveal table (the `p` action)
            await pilot.pause()
            on = app.check_action("toggle_filter", ())  # now available
            return off, hidden_while_off, on

    off, hidden_while_off, on = asyncio.run(_run())
    assert off is False  # binding hidden when table off
    assert hidden_while_off is False  # `/` did not open the input
    assert on is True  # binding available once table shown
