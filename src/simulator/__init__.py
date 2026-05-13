"""Trajectory simulator package.

Generates historical narrative state for the trajectory chart and daily
briefing inbox features.  The package is structured as:

- spec.py            — Pydantic schema for trajectory YAML; load/save; collision detection
- primitives.py      — Five trajectory shape functions (Phase 2)
- signal_synthesis.py — health-band → signal plan mapper (Phase 3)
- executor.py        — batch executor; loads spec, processes pending entries (Phase 4)
- author.py          — rich TUI for interactive spec authoring (Phase 5)
- bootstrap.py       — DB-state → proposed spec heuristic (Phase 5.5)
"""
