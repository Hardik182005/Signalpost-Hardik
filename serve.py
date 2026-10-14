#!/usr/bin/env python3
"""Launcher for the Signalpost web interface.

    uv run --extra ui serve.py --input out/submission-profiles.jsonl --port 8765

The same shim as `main.py`, for the same reason: the project is deliberately not
installed as a package (`[tool.uv] package = false`), so `src/` is not on
`sys.path` and must be put there before the import. No argument parsing and no
behaviour live here; `signalpost.ui.cli` owns both.

The interface is read-only over run output. It opens the `.jsonl` file it is given
and the measured `*-report.json` files beside it, and writes nothing back. The one
action that performs work — running the pipeline for a company — is off unless the
operator passes `--enable-research`, and even then it writes only into its own
scratch directory.

Windows note: the console defaults to cp1252, which raises UnicodeEncodeError on
Norwegian company names. stdout and stderr are reconfigured to UTF-8 here so the
documented command works on a reviewer's machine without an environment variable.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # An unusual stream (a pipe opened in binary, a captured buffer) is not a
            # reason to refuse to run; the output just stays in the platform encoding.
            pass

from signalpost.ui.cli import main  # noqa: E402  (path setup must precede the import)

if __name__ == "__main__":
    raise SystemExit(main())
