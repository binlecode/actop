import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.local
def test_actop_runs_and_handles_sigint():
    # Run actop via subprocess, send SIGINT (Ctrl+C) and check it exits cleanly
    process = subprocess.Popen(
        [sys.executable, "-m", "actop.actop", "--interval", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Give it a bit more time to initialize and render the first frame
        time.sleep(4)

        # Send SIGINT to simulate Ctrl+C
        process.send_signal(signal.SIGINT)

        # Wait for it to quit gracefully
        _stdout, stderr = process.communicate(timeout=3)

        # Textual manages its own lifecycle: exits 0 on clean quit, or 130 if
        # the outer main() catches KeyboardInterrupt first.
        assert process.returncode in (0, 130)

        # Textual renders to the terminal's alternate screen buffer, not plain
        # piped stdout, so we verify no fatal Python tracebacks in stderr instead.
        assert "Traceback" not in stderr

    except subprocess.TimeoutExpired:
        process.kill()
        _stdout, stderr = process.communicate()
        assert False, (
            f"actop did not quit within timeout after SIGINT. stderr: {stderr}"
        )
