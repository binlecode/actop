# TODO: `--theme` flag + runtime theme keybinding

**Status:** plan · **Target:** v1.7.0 · **Created:** 2026-08-11

## Summary

Add a `--theme` CLI flag and a runtime key binding (`T`) to select and cycle
Textual-level application themes (the UI chrome — header/footer/border/background/text
colors). This is distinct from the existing `--palette` flag, which controls **chart
gradient** colors (thermal/viridis/mono for `BrailleChart` sparklines). The two are
orthogonal: `--palette` picks how hot/cold data values are colored inside the charts,
while `--theme` picks how the surrounding app shell looks.

Textual ships 11 built-in themes; `actop` currently uses the framework default
(`textual-dark`) with no override. This feature exposes that latent surface.

---

## 1. Why this is the right time

The project already tracks this gap:

- `docs/REVIEW-architecture-comparison.md` §4 notes that a runtime theme cycle
  keybind was "deliberately deferred as optional" in v1.4.1 when `--palette` shipped.
  The converged peer feature is *decorative* theme cycling (mactop party mode, macmon
  6 themes via `c`), and `actop` deliberately chose to ship *accessibility* palettes
  first. A runtime cycle keybind was noted as "a purely additive follow-on."
- `docs/REVIEW-tui-frameworks.md` notes the "Config/Themes tab (Catppuccin/Nord
  toggles) was never built."
- `docs/SPEC-system.md` §5.2: "The palette is set once at construction ... a runtime
  keybind is a deferred, purely additive follow-on."

**The follow-on is now.** No architectural changes needed — Textual's `App.theme`
property + `register_theme()`/`BUILTIN_THEMES` do all the heavy lifting. The existing
CSS already uses `$accent`, `$surface`, `$text-muted`, etc. — these resolve from the
active theme automatically.

---

## 2. Architecture: what changes

### 2.1 New: `--theme` flag in `actop/actop.py`

```python
# In build_parser(), after --palette argument (line 77):

parser.add_argument(
    "--theme",
    choices=[
        "textual-dark",
        "textual-light",
        "nord",
        "dracula",
        "tokyo-night",
        "monokai",
        "gruvbox",
        "catppuccin-mocha",
    ],
    default="textual-dark",
    help="Textual app theme (UI chrome: header/footer/borders/background). "
    "Orthogonal to --palette (chart gradient). Cycle live with the 'T' key.",
)
```

**Theme selection rationale:**
- `textual-dark` — default; preserves current appearance exactly
- `textual-light` — light mode (differentiated offer; mactop/macmon don't have it)
- `nord`, `dracula`, `tokyo-night`, `monokai`, `gruvbox`, `catppuccin-mocha` —
  the 6 most popular terminal color schemes by community adoption. All are
  well-tested built-in Textual themes (no write-your-own-CSS burden).

Deliberately excluded from the curated set (but still available via `App.theme = "name"` in
custom scripting — no doors locked): `flexoki`, `catppuccin-latte`, `catppuccin-frappe`.

### 2.2 New: `theme` field in `actop/config.py`

```python
# In DashboardConfig frozen dataclass, add after `palette`:
theme: str

# In create_dashboard_config(), add after palette=... line:
theme = (getattr(args, "theme", "textual-dark"),)
```

### 2.3 Modified: `actop/tui/app.py`

#### 2.3.1 Set the theme at startup

In `ActopApp.__init__`, after `self.title = "actop"`:

```python
from textual.theme import BUILTIN_THEMES

# Register all built-in themes (idempotent; Textual registers them in __init__
# too, but doing it explicitly makes the intent clear and guards against a future
# Textual version that might not auto-register).
for name, theme in BUILTIN_THEMES.items():
    self.register_theme(theme)

# Apply the configured theme. Must happen before compose() so the CSS
# variables resolve correctly on first paint.
self.theme = self._config.theme
```

**Timing note:** `self.theme` can be set in `__init__` — once `App.__init__` has run
and themes are registered. Setting it here means the theme is applied before the first
`compose()`, so there's no flash of the default theme.

#### 2.3.2 Add key binding

In `_LETTER_BINDINGS`, add after the `t` entry:

```python
(("T", "cycle_theme", "Theme"),)
```

Note: `T` (capital) is the **sole** binding. The existing `_LETTER_BINDINGS` pattern
auto-generates uppercase aliases for lowercase keys — but here the primary binding
is `T` itself (shift-t), deliberately distinct from `t` (toggle processes). The
uppercase alias generator only fans out lowercase entries, so `T` stays as-is and
does not duplicate.

If you want a lowercase fallback, add a hidden alias:

```python
(Binding("t", "cycle_theme", "Theme", show=False),)
```

But better: keep `T` only — the existing pattern is `t` = processes, shift-`T` = theme.
Users already know `shift` for layout (`L` alias), glyph (`G` alias), cores (`C` alias).

**Footer display:** `T  Theme` will appear in the status bar footer alongside the
existing bindings.

#### 2.3.3 Add action handler

```python
# The themes to cycle through at runtime. Same set as the --theme choices,
# in the same order for consistency. Stored as a class constant so it's
# testable without instantiating the app.
_THEME_CYCLE = [
    "textual-dark",
    "nord",
    "dracula",
    "tokyo-night",
    "monokai",
    "gruvbox",
    "catppuccin-mocha",
    "textual-light",
]


def action_cycle_theme(self) -> None:
    """Advance to the next theme in the cycle. Wraps around."""
    names = self._THEME_CYCLE
    try:
        idx = names.index(self.theme)
    except ValueError:
        idx = 0
    next_idx = (idx + 1) % len(names)
    self.theme = names[next_idx]
    self.notify(
        f"Theme: {names[next_idx]}",
        timeout=2,
    )
```

**Why a fixed cycle list instead of enumerating `available_themes`?**
- Deterministic order (not dict-insertion-order across Textual versions).
- Excludes themes that aren't curated for `--theme` (catppuccin-latte, flexoki,
  catppuccin-frappe — they exist as built-ins but aren't in the CLI choices).
- A user who picked `--theme dracula` at startup and presses `T` gets `tokyo-night`,
  not a surprise theme they never chose.

#### 2.3.4 Update HELP_TEXT

In the keybinding section of `HELP_TEXT`, after the `t` line:

```text
  T          Cycle app theme (textual-dark → nord → dracula → … → textual-light)
```

In the "Layout presets" section, add:

```text
[b]Themes[/b]

  --theme NAME          Set the app theme at launch. Choices: textual-dark
                        (default), textual-light, nord, dracula, tokyo-night,
                        monokai, gruvbox, catppuccin-mocha. Press T to cycle
                        live.

  The --theme flag controls the UI chrome (header, footer, borders, text
  colors). The --palette flag controls chart gradient colors (thermal,
  viridis, mono). The two are independent: any theme works with any palette.
```

#### 2.3.5 Accessibility: honor `NO_COLOR`

The `--theme` flag is for app chrome, not chart content. It should still apply
under `NO_COLOR` — the theme controls layout-relevant CSS variables like
`$accent` (border drawing), `$surface` (panel backgrounds), and `$text-muted`
(readability). `NO_COLOR` only degrades the chart gradient inside `BrailleChart`
(already handled by `resolve_color_mode()`). No extra work needed here.

#### 2.3.6 No CSS changes needed

The existing CSS uses only Textual's built-in CSS variables (`$accent`, `$surface`,
`$text-muted`, `$background`, `$foreground`). Textual resolves these from the active
theme's `ColorSystem`. Zero CSS edits required.

---

## 3. Files changed (complete list)

| File | Change |
|------|--------|
| `actop/actop.py` | Add `--theme` argument to `build_parser()` |
| `actop/config.py` | Add `theme: str` field to `DashboardConfig`, wire in `create_dashboard_config()` |
| `actop/tui/app.py` | Register themes + set initial theme in `__init__`, add `T` binding + `action_cycle_theme()`, update `HELP_TEXT`, add `_THEME_CYCLE` class constant |
| `tests/test_cli_contract.py` | Add: help output lists `--theme`, unknown theme rejected, default is `textual-dark` |
| `tests/test_tui_app.py` | Add: pressing `T` cycles the theme through the wheel and wraps around, uppercase `T` is bound |
| `CHANGELOG.md` | Entry under Unreleased |
| `pyproject.toml` | Bump patch version |

---

## 4. Implementation steps (ordered)

### Step 1: `actop/config.py` — add `theme` field

```python
# In the DashboardConfig dataclass, after `palette: str`:
theme: str

# In create_dashboard_config(), after palette line:
theme = (getattr(args, "theme", "textual-dark"),)
```

### Step 2: `actop/actop.py` — add `--theme` argument

Insert after the `--palette` argument block (after line 77):

```python
parser.add_argument(
    "--theme",
    choices=[
        "textual-dark",
        "textual-light",
        "nord",
        "dracula",
        "tokyo-night",
        "monokai",
        "gruvbox",
        "catppuccin-mocha",
    ],
    default="textual-dark",
    help=(
        "Textual app theme for UI chrome (header, footer, borders, "
        "background). Orthogonal to --palette which controls chart "
        "gradient colors. Cycle live with the 'T' key."
    ),
)
```

Add assertion in `test_cli_contract.py::test_cli_help_runs_and_exposes_show_cores_as_flag`:

```python
assert (
    "--theme {textual-dark,textual-light,nord,dracula,tokyo-night,monokai,gruvbox,catppuccin-mocha}"
    in result.stdout
)
```

### Step 3: `actop/tui/app.py` — theme initialization

In `ActopApp.__init__`, after `self.title = "actop"` (line 266):

```python
# Apply the configured theme. Register all built-in themes first (Textual
# auto-registers them, but an explicit registration makes the intent clear
# and survives a future framework change).
from textual.theme import BUILTIN_THEMES

for theme in BUILTIN_THEMES.values():
    self.register_theme(theme)
self.theme = self._config.theme
```

### Step 4: `actop/tui/app.py` — key binding + action

Add `_THEME_CYCLE` as a class constant after `_SORT_CYCLE` (after line 35):

```python
_THEME_CYCLE = [
    "textual-dark",
    "nord",
    "dracula",
    "tokyo-night",
    "monokai",
    "gruvbox",
    "catppuccin-mocha",
    "textual-light",
]
```

In `_LETTER_BINDINGS`, after `("t", "toggle_processes", "Processes")`:

```python
(("T", "cycle_theme", "Theme"),)
```

Add action method after `action_toggle_cores` (after line 402):

```python
def action_cycle_theme(self) -> None:
    """Advance to the next app theme. Wraps around the curated cycle."""
    try:
        idx = self._THEME_CYCLE.index(self.theme)
    except ValueError:
        idx = 0
    next_idx = (idx + 1) % len(self._THEME_CYCLE)
    self.theme = self._THEME_CYCLE[next_idx]
    self.notify(f"Theme: {self.theme}", timeout=2)
```

### Step 5: `actop/tui/app.py` — update HELP_TEXT

After the `t  Toggle the process table` line (line 99), add:

```python
  T          Cycle app theme (dark → nord → dracula → … → light)
```

After the "Layout presets" section (after line 113), add:

```python
[b]Themes[/b]

  T          Cycle through built-in app themes: textual-dark
             (default), nord, dracula, tokyo-night, monokai, gruvbox,
             catppuccin-mocha, textual-light. The theme controls the UI
             chrome (header, footer, borders, text colors) — independent
             of --palette (chart gradient). Press T to cycle live.
```

### Step 6: Tests

#### `tests/test_cli_contract.py`

Add to `test_cli_help_runs_and_exposes_show_cores_as_flag`:

```python
assert "--theme " in result.stdout  # flag exists
assert "textual-dark" in result.stdout  # default visible
```

New test:

```python
def test_cli_rejects_unknown_theme():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--theme", "nonexistent"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice: 'nonexistent'" in result.stderr


def test_cli_accepts_known_themes():
    parser = build_parser()
    for theme in (
        "textual-dark",
        "textual-light",
        "nord",
        "dracula",
        "tokyo-night",
        "monokai",
        "gruvbox",
        "catppuccin-mocha",
    ):
        args = parser.parse_args(["--theme", theme])
        assert args.theme == theme
    # Default when not specified
    args = parser.parse_args([])
    assert args.theme == "textual-dark"
```

#### `tests/test_tui_app.py`

New tests:

```python
def test_theme_binding_cycles_through_curated_set():
    """Pressing T cycles the app theme through the curated cycle and wraps."""

    async def _run():
        app = ActopApp(build_parser().parse_args(["--theme", "nord"]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_focus(None)
            assert app.theme == "nord"
            await pilot.press("T")
            await pilot.pause()
            assert app.theme == "dracula"
            # Advance to the end and wrap
            for _ in range(
                7
            ):  # nord→dracula→tokyo→monokai→gruvbox→catppuccin→light→dark
                pass  # already advanced once, need 6 more to get to dark
            # Instead, simpler: wrap-around test
            app.theme = "textual-light"  # last in cycle
            await pilot.press("T")
            await pilot.pause()
            assert app.theme == "textual-dark"  # wraps to first
            return True

    assert asyncio.run(_run())


def test_theme_defaults_to_textual_dark():
    """Without --theme, the app starts in textual-dark (current behavior
    preserved)."""

    async def _run():
        app = ActopApp(build_parser().parse_args([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            return app.theme

    theme = asyncio.run(_run())
    assert theme == "textual-dark"


def test_status_bar_exposes_theme_binding():
    """The 'T  Theme' binding must appear in the status bar footer."""
    keys = {(b[0] if isinstance(b, tuple) else b.key) for b in ActopApp.BINDINGS}
    assert "T" in keys
```

### Step 7: Update `test_cli_contract.py` context assertion

In `test_help_overlay_documents_keys_metrics_and_alert_tokens`, add:

```python
assert "Theme" in help_text  # the T keybinding is documented
assert "textual-dark" in help_text  # themes are listed in the help overlay
```

### Step 8: CHANGELOG + version bump

```
## [1.7.0] - 2026-08-11

### Added
- `--theme` flag to select Textual app theme at launch (UI chrome:
  header, footer, borders, text colors). Choices: textual-dark (default),
  textual-light, nord, dracula, tokyo-night, monokai, gruvbox,
  catppuccin-mocha.
- `T` key binding to cycle through themes live during a session.
- Theme name shown in a toast notification on each change.
```

Bump `pyproject.toml` version from current to `1.7.0`.

---

## 5. Verification checklist

- [ ] `.venv/bin/python -m actop.actop --help` shows `--theme` with choices + default
- [ ] `.venv/bin/python -m actop.actop --theme nord` launches with nord-colored chrome
- [ ] `.venv/bin/python -m actop.actop --theme light` launches with light mode chrome
- [ ] Pressing `T` in a running session cycles through themes with a toast notification
- [ ] Theme wraps around from textual-light back to textual-dark
- [ ] `--theme` + `--palette monochrome` works: grey charts in a nord shell
- [ ] `--theme` rejected for unknown values (exit code 2, argparse error)
- [ ] Status bar footer shows `T  Theme`
- [ ] Help overlay (`?`) documents the `T` binding and lists theme names
- [ ] `.venv/bin/pytest -q` passes all tests
- [ ] `.venv/bin/ruff check --fix . && .venv/bin/ruff format .` clean
- [ ] Screenshot in PR: `actop --theme dracula --palette viridis` showing the
  contrast between dracula chrome and viridis chart gradient

---

## 6. Design decisions (why this way)

### Why Textual Theme objects, not CSS class toggles?
Textual's `Theme` system is purpose-built for this: it sets CSS variables that every
widget already references (`$accent`, `$surface`, etc.). A CSS-class approach would
require duplicating every style rule per theme. The Theme approach is zero-CSS-change.

### Why `T` (shift-T) and not another letter?
- `t` is already taken by "toggle process table"
- `T` (shift-T) follows the existing pattern: `l`/`L` both work for layout,
  `g`/`G` both work for glyph, `c`/`C` both work for cores
- The uppercase alias generator in `BINDINGS` only fans out lowercase entries,
  so adding `T` to `_LETTER_BINDINGS` is correct — it's the canonical binding

### Why a curated cycle, not all 11 built-in themes?
- 11 themes is too many to cycle through; the user sees no difference between
  similar dark themes and gets lost
- The 8 curated themes cover the major terminal aesthetics: default, light,
  and 6 most popular open-source color schemes
- Advanced users can still set any built-in theme via `--theme` on the CLI
  (argparse `choices` validates against the curated set; the cycle uses the
  same set for consistency)

### Why a toast notification, not a status-bar update?
- The status bar already carries alert tokens, span, and session energy —
  adding transient theme info there is noisy
- A toast (Textual's `self.notify()`) is the standard pattern for transient
  state changes (used by Textual's own built-in theme toggle: `Ctrl+P` →
  "Change theme" command palette)
- 2-second timeout keeps it unobtrusive

### Why register explicit themes instead of using auto-registered ones?
- Textual 2.x auto-registers `BUILTIN_THEMES` in `App.__init__`, but an
  explicit `register_theme()` call makes the dependency clear and survives
  future framework changes
- It's idempotent — no harm in registering twice

---

## 7. Edge cases

| Case | Handling |
|------|----------|
| User passes `--theme` with an unknown value | argparse `choices` rejects it; exit code 2, stderr: "invalid choice" |
| User sets `App.theme` to a name not in `_THEME_CYCLE` (e.g. via custom scripting) | `action_cycle_theme()` falls back to index 0 (textual-dark) when `self.theme` is not found in the cycle list |
| Terminal with `NO_COLOR=1` | No effect on `--theme` — the theme controls CSS variables for layout, not color output. Charts still degrade via `resolve_color_mode()` (existing behavior) |
| Very narrow terminal (< 80 cols) | Themes look consistent at any width; CSS `$accent` resolves correctly regardless of terminal size |
| User presses `T` rapidly | Each press advances one step in the cycle; Textual's reactive `self.theme` setter handles rapid changes correctly (CSS invalidation + refresh) |
| `--theme textual-light` while macOS is in dark mode | The terminal emulator's background is controlled by the terminal app, not actop. Textual's `textual-light` theme uses light-colored chrome regardless of OS mode (no auto-detect — explicit user choice) |
