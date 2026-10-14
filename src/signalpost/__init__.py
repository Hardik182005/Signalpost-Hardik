"""Signalpost — evidence-anchored Norwegian company research agent.

The package is layered so the evaluator path never depends on the UI, a hosted
database or developer machine state:

    budget    -> atomic request / cost / wall-clock accounting
    fetch     -> the only outbound egress point (SSRF, robots, caching, snapshots)
    models    -> typed claims, evidence, snapshots and envelopes
    registry  -> frozen official snapshot + live Brreg modules
    discovery -> website candidate generation
    identity  -> exact-entity publication gate and candidate ledger
    extract   -> structured -> DOM -> text -> PDF extraction layers
    refresh   -> stable claim keys, typed change events, materiality
    synthesis -> deterministic grounded summary
    pipeline  -> per-company orchestration
    runner    -> batch execution, checkpointing and terminal envelopes
"""

__version__ = "1.0.0"

AGENT_NAME = "Signalpost"
PIPELINE_VERSION = "signalpost/1.0.0"
