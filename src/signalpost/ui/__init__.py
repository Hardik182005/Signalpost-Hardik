"""Read-only web interface over Signalpost run outputs.

The evaluator path must not depend on the frontend, so nothing under
``signalpost.ui`` is imported by the pipeline, the runner or the CLI shim in
``main.py``. The dependency arrow points one way only: the UI reads the engine's
models and its JSONL output, never the reverse.

Everything rendered here comes from a run that already happened. The interface
loads terminal envelopes from a ``.jsonl`` file and the measured ``*-report.json``
files beside it; it does not estimate, sample or invent any value. The only path
that performs new work is the optional research/batch action, which is disabled
unless the operator starts the server with ``--enable-research`` and which writes
exclusively into a scratch directory it creates itself.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
