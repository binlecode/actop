#!/usr/bin/env python3
"""Clean a raw `tmux capture-pane -p` frame of the actop TUI into a diagram
ready to paste (verbatim) into a Markdown code fence.

Usage:
    tmux capture-pane -t SESSION -p > raw.txt
    python clean_capture.py raw.txt              # side-by-side frame (grid / +table)
    python clean_capture.py raw.txt --compress   # single-column frame (stack)

What it does (always):
  - turns the leading splash spinner glyph ("⭘") into spaces,
  - strips a dashboard scrollbar thumb block that renders at/after a box border,
  - right-trims every line (tmux pads to the pane width),
  - drops trailing blank lines.

--compress (SINGLE-COLUMN captures only, e.g. the `stack` preset): collapses a
run of >=2 blank BrailleChart rows down to one representative row, so a tall
all-full-width frame fits a doc. NEVER use it on a side-by-side frame (grid, or
dashboard+table): the two columns share text lines, so removing an interior row
on one side desyncs the other and the box borders tear. Side-by-side frames must
be pasted VERBATIM.
"""

import argparse
import re
import sys

BLANK = "⠀"  # U+2800 blank Braille cell


def strip_common(lines):
    out = []
    for ln in lines:
        ln = ln.rstrip()
        if ln.startswith(" ⭘"):  # " ⭘" splash spinner -> spaces
            ln = "  " + ln[2:]
        # scrollbar thumb block chars after a closed box or right border
        ln = re.sub(r"(╰[─]*╯)[▀-▟]+$", r"\1", ln)
        ln = re.sub(r"(│)[▀-▐]{1,2}$", r"\1", ln)
        out.append(ln)
    while out and out[-1].strip() == "":
        out.pop()
    return out


def is_blank_chart(ln):
    m = re.match(r"^│(.*)│$", ln)  # │ ... │
    if not m:
        return False
    inner = m.group(1)
    return BLANK in inner and inner.strip(BLANK + " ") == ""


def compress_blank_charts(lines):
    out, i = [], 0
    while i < len(lines):
        if is_blank_chart(lines[i]):
            out.append(lines[i])  # keep one representative row
            while i < len(lines) and is_blank_chart(lines[i]):
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="raw capture file (default: stdin)")
    ap.add_argument(
        "--compress",
        action="store_true",
        help="collapse blank chart rows (single-column captures ONLY)",
    )
    args = ap.parse_args()
    raw = (open(args.path) if args.path else sys.stdin).read()
    lines = strip_common(raw.split("\n"))
    if args.compress:
        lines = compress_blank_charts(lines)
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
