# TODO: Sudo Mode — Elevate to Root for Per-Process GPU/Energy Attribution

Status: **planning** · Created: 2026-08-10

## Motivation

actop without sudo provides system-wide GPU, power, and per-core metrics
via unprivileged IOKit/IOReport APIs. However, per-process GPU time share
(`gpu_time_share`) and per-process attributed wattage (`attributed_w`)
require root — those fields read `0.0` / `None` without sudo, as the
underlying `powermetrics` process-attribution path needs elevated access.

Users should be able to opt in to elevated mode from two paths:
1. At launch (`--sudo` CLI flag)
2. Mid-session via a keybinding in the TUI

## Feature Specification

### 1. CLI: `--sudo` startup flag

  - Add `--sudo` (`store_true`) to the argument parser in `actop/actop.py`.
  - When `--sudo` is present and `os.geteuid() != 0`:
    - Re-execute the same command line + `--sudo` under `sudo`.
    - Pass through all original arguments.
    - The launched sudo child inherits the terminal (stdin/stdout/stderr).
    - The parent exits with the child's return code.
  - When `os.geteuid() == 0` (already root, e.g. via `sudo actop`):
    - `--sudo` is a no-op — sampling already uses the privileged path.
  - Edge cases:
    - `sudo` not available → print a clear error to stderr and exit 1.
    - User cancels sudo password prompt → exit with the sudo error code.

Implementation sketch (`actop/actop.py`):
```python
def _exec_sudo(argv: list[str]) -> Never:
    """Re-execute the current CLI under sudo and replace the process."""
    try:
        os.execvp("sudo", ["sudo", *argv])
    except FileNotFoundError:
        print("error: sudo is not available on this system.", file=sys.stderr)
        sys.exit(1)
```

### 2. TUI keybinding: `u` for sudo elevation

  - Add `"u"` to `_LETTER_BINDINGS` in `tui/app.py`:
    `("u", "elevate_sudo", "Elevate")`
  - When pressed and already root, show a transient notification (Textual
    `notify()`): "Already running as root."
  - When pressed and not root, push a `SudoElevationScreen` (ModalScreen)
    containing:
    - A brief explanation of why elevation is offered (per-process GPU attribution).
    - A **password Input field** (`password=True` for masked input).
    - **Clear message**: "Your password is used only to restart actop under
      sudo. It is never written to disk, cached, or saved."
    - Two paths on submit:
      1. If the password is empty, dismiss the modal (cancel).
      2. If a password is entered, validate it via a subprocess (e.g. `sudo -k;
         sudo -S -v <<< "$password"` to check). On failure, flash an error and
         let the user retry. On success, serialise the current config
         (filter, sort, layout, pause state, show-processes, show-cores) into
         a temporary JSON file under `/tmp/actop-state-<pid>.json`, then
         `os.execvp("sudo", ["sudo", "-S", *argv, "--sudo", "--resume",
         tmp_path])` with the password piped to stdin.
  - The new elevated process detects `--resume <path>`, reads the state file,
    applies the saved settings, and deletes the file.

### 3. `SudoElevationScreen` ModalScreen

  - New class in `tui/app.py` (or a new `tui/sudo.py` if it grows).
  - Layout (Textual compose):
    - Title: "Elevate to Root"
    - Explanation: "Per-process GPU time and attributed power need root
      privileges."
    - Disclaimers: "Password is never written to disk, cached, or saved."
    - Input field with `password=True`, placeholder="Enter sudo password"
    - Submit button ("Elevate") and Cancel button / Escape key
  - Key handling:
    - `escape` → dismiss
    - `enter` / button click → validate & restart
  - Validation flow:
    1. Pipe password to `sudo -S -v 2>&1` to validate.
    2. If invalid → show error label, keep modal open.
    3. If valid → write state file, execvp.
  - Styling: Use existing Textual theme colours (no new palette).

### 4. `--resume` mechanism

  - Hidden CLI flag (`--resume PATH` in `build_parser()`):
    - Reads the temporary JSON state file.
    - Applies `show_processes`, `proc_filter`, `sort_column`, `chart_glyph`,
      `layout`, `show_cores`, `paused` to the `DashboardConfig` / app
      attributes.
    - Deletes the state file immediately after reading.
  - State file schema:
    ```json
    {
      "version": 1,
      "show_processes": true,
      "proc_filter": "Zed|Visual Studio Code",
      "sort_column": "cpu_percent",
      "chart_glyph": "block",
      "layout": "grid",
      "show_cores": false,
      "paused": false
    }
    ```

### 5. Process Sampling Behaviour

  - `sampler.py` / `api.py`: No changes needed. The existing
    `powermetrics`-backed process sampler already works when called as root —
    the elevated process simply gets the privileged codepath for free.
  - Verify: test once to confirm `gpu_time_share` and `attributed_w`
    populate when running `sudo actop --show-processes --json --samples 1`.

## Implementation Order

| Step | Description | Lines touched |
|------|-------------|---------------|
| 1 | Add `--sudo` CLI flag + `_exec_sudo()` to `actop.py` | ~15 |
| 2 | Add `--resume PATH` hidden flag + state-file reader to `actop.py` | ~25 |
| 3 | State serialisation helpers (`dump_state` / `load_state`) in `config.py` or new `tui/state.py` | ~30 |
| 4 | Add `"u"` binding + `action_elevate_sudo()` to `ActopApp` | ~40 |
| 5 | Create `SudoElevationScreen` ModalScreen (compose, validation, re-exec) | ~80 |
| 6 | Wire `--resume` into TUI init path in `tui/app.py` | ~15 |
| 7 | Integration test: `actop --sudo --json --samples 1` + check for privileged fields | ~10 |

## Risks / Open Questions

- **Terminal takeover**: `os.execvp` replaces the process in-place. If the
  TUI's terminal mode (raw/cbreak) isn't fully restored before exec, the sudo
  child may inherit a garbled terminal. Mitigation: call `self.exit()` to let
  Textual tear down terminal state, then use a post-exit callback to exec.
  Alternate: use `subprocess.Popen` + `os.waitpid` instead of exec, but that
  forks an extra process. Decision needed.
- **Password validation**: `sudo -v` refreshes the timestamp. The `-k` flag
  (reset timestamp) before validation is recommended to avoid side effects.
- **State file race**: `/tmp/actop-state-<pid>.json` — if two instances are
  elevated simultaneously, PIDs ensure uniqueness. Cleanup on SIGTERM via
  `atexit` handler.
- **`--resume` visibility**: Should NOT appear in `--help` output. Use
  `argparse.SUPPRESS` for the help string.

## References

- `tui/app.py:237-260` — existing `_LETTER_BINDINGS` / `BINDINGS` pattern
- `tui/app.py:168-183` — existing `HelpScreen` ModalScreen pattern
- `tui/app.py:305,426-438` — existing filter Input widget + toggle pattern
- `actop.py:63-85` — existing CLI arg definitions
- `config.py:43-44` — `DashboardConfig` fields for `show_processes` / `process_filter_pattern`
