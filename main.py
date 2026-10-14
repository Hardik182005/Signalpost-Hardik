#!/usr/bin/env python3
"""Single entry point for the Signalpost agent.

    uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/run.jsonl

The project is deliberately not installed as a package (`[tool.uv] package = false`),
which keeps `uv sync` to dependency resolution and makes the checkout itself the unit
of reproduction. The cost of that choice is that `src/` is not on `sys.path` by
default, so this shim puts it there before importing anything from the package. That
is the whole job: no argument parsing, no behaviour, nothing a reviewer has to read
past to trust the run command.

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

from signalpost.runner import main  # noqa: E402  (path setup must precede the import)

if __name__ == "__main__":
    raise SystemExit(main())
