# Contributing

This repository complements [JuPedSim Web](https://app.jupedsim.org). It
hosts the public Docker setup, scenario-scripting workflow, V&V test
suites, and example notebooks. The web app itself is private; this repo
is community-facing.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). From the
repository root:

```bash
uv sync --extra dev
```

This creates `.venv/` and installs runtime + test dependencies.

## Running tests

A single command from the repository root runs everything:

```bash
uv run --extra dev pytest
```

This collects:

- `tests/vv/` — V&V suite (RiMEA, IMO).
- `standards/tests/` — webapp scenario examples + scenario API tests.

Run a subset:

```bash
uv run --extra dev pytest tests/vv/test_rimea.py        # RiMEA only
uv run --extra dev pytest standards/tests                # standards only
uv run --extra dev pytest tests/test_api_surface.py      # API surface smoke
```

Always run the relevant subset locally before pushing.

## Repository layout

- `standards/` — per-standard notebooks, scenario files, and tests.
  - `general/`, `rimea/`, `imo/`, `iso/`, `nist/` — one folder per standard.
  - Each standard owns `scenario_files/` (exported ZIPs from the webapp)
    and any notebooks demonstrating the scenarios.
  - `tests/` — pytest covering webapp scenarios + the scenario API.
- `tests/vv/` — programmatic V&V suite (one test module per standard).
- `docker/` — public local-deployment setup for the webapp.
- `docker/llm-bridge/` — experimental HTTP bridge for driving the local viewer from scripts or LLM agents.
- `geometries/` — public geometry examples (DXF, IFC, WKT).
- `.github/workflows/` — CI: per-standard test workflows, scenario
  examples, scheduled notebook execution, and upstream-drift detection.

## Adding a new standard

1. Create `standards/<name>/scenario_files/` with the exported ZIPs.
2. Add notebooks at `standards/<name>/*.ipynb`. Follow the layout of
   `standards/rimea/`.
3. Add a programmatic test module at `tests/vv/test_<name>.py` using
   `vv_helpers.py`.
4. Add a CI workflow at `.github/workflows/<name>.yml` mirroring
   `rimea.yml` / `imo.yml`.
5. Register the new tests in `tests/vv/test_registry.py` if applicable.

## Scenarios

All scenarios are authored in the JuPedSim web app and exported as ZIPs
containing `config.json` and `geometry.wkt`. They are loaded with
`jupedsim_scenarios.load_scenario(...)`. Do not hand-edit exported
ZIPs; re-export from the webapp instead.

## Notebooks

Notebooks are committed **with outputs** so readers can see results
without re-running heavy simulations. Trade-offs:

- Re-running a notebook produces large diffs in cell outputs.
- Each notebook has an "Executed on …" timestamp cell near the top.
- The weekly `notebooks.yml` CI re-executes every notebook to catch
  silent breakage; failures open a tracking issue.

When reviewing notebook diffs, [`nbdime`](https://nbdime.readthedocs.io/)
gives a readable cell-level view (install separately):

```bash
pipx install nbdime
nbdiff --ignore-outputs path/to/notebook.ipynb
```

If you need to commit a notebook without re-running it, only edit
markdown cells.

## Upstream drift

`jupedsim-scenarios` is the load-bearing dependency. Two safety nets:

- `tests/test_api_surface.py` — fast smoke test of public symbols.
- `.github/workflows/upstream-drift.yml` — weekly run against the
  latest release; opens an issue on incompatibility.

When bumping `jupedsim-scenarios`, run the API surface test first.

## Git hygiene

- Atomic commits with imperative subject lines.
- Subject ≤ 70 chars, optional body explains the *why*.
- No `--no-verify`. If a hook fails, fix it.
- Prefer PRs for anything beyond chore-level fixes.
