#!/usr/bin/env python3
"""Drive a door-bottleneck evacuation through the bridge and measure its flow.

What this demonstrates: the bridge turns the browser viewer into a *measurable*
experiment. We build a 100-agent room with a single 1 m exit, run it via the
viewer's own Run Simulation button (over HTTP), pull the resulting SQLite
trajectories back, and compute two things you cannot read off the GUI:

  1. the evacuation curve N(t) — cumulative agents that have left, over time;
  2. the steady-state specific flow through the exit, in ped/(s*m).

Two entry points:
  python bottleneck_flow.py --run            # drive a fresh sim, then analyse
  python bottleneck_flow.py --sqlite FILE    # re-analyse an existing SQLite

Requirements: pedpy + matplotlib (analysis). The --run driver is stdlib-only.

Important: --run needs a *freshly loaded* viewer tab open at
http://localhost:8081/draw. The bridge drives one simulation per fresh viewer
session reliably; it does not support rapid re-parametrisation in one session
(clearing the scene between runs is unreliable). Reload the tab before each run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.request

BRIDGE = "http://127.0.0.1:8090"
TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "scenario_room1.json"
DOOR_WIDTH_M = 1.0            # exit opening y in [2, 3] on the x=10 wall
MEASURE_X = 9.5              # full-height line just inside the exit throat
N_AGENTS = 100
OUT_DIR = pathlib.Path(__file__).resolve().parent


# --- bridge HTTP helpers (standard library only) ---------------------------

STALE_HELP = (
    "The bridge drives one simulation per freshly loaded viewer session. "
    "Reload the viewer tab at http://localhost:8081/draw and retry. If it "
    "persists, clear the bridge's in-memory state:\n"
    "  docker compose -f docker/llm-bridge/nobuild/docker-compose.yml restart bridge\n"
    "then reload the tab and run again.")


def _post(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        BRIDGE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)            # bridge returns JSON on 4xx too
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {error.code}"}


def _get(path: str) -> dict:
    with urllib.request.urlopen(BRIDGE + path, timeout=30) as resp:
        return json.load(resp)


def _get_bytes(path: str) -> bytes:
    with urllib.request.urlopen(BRIDGE + path, timeout=120) as resp:
        return resp.read()


def _wait(poll, done, tries: int = 150, delay: float = 2.0):
    for _ in range(tries):
        value = poll()
        if done(value):
            return value
        time.sleep(delay)
    raise TimeoutError("bridge did not reach the expected state in time")


# --- run one simulation via the viewer -------------------------------------

def run_bottleneck(n_agents: int) -> pathlib.Path:
    active = (_get("/api/simulations/latest").get("simulation") or {}).get("status")
    if active in ("queued", "accepted", "running"):
        sys.exit(f"a simulation is already active ({active}).\n{STALE_HELP}")

    doc = json.loads(TEMPLATE.read_text())
    config = doc["config"]
    params = config["distributions"]["dist-room"]["parameters"]
    params["number"] = n_agents
    params["use_premovement"] = False           # release all agents at t=0
    settings = config["config"]["simulation_settings"]
    settings["numberOfSimulations"] = 1
    settings["baseSeed"] = 42

    result = _post("/api/validate",
                   {"config": config, "geometry_wkt": doc["geometry_wkt"]})
    if not result.get("ok"):
        sys.exit(f"validation failed: {result.get('errors')}")
    scenario_id = result["scenario"]["id"]
    _wait(lambda: _get("/api/scenarios/latest"),
          lambda v: (v.get("scenario") or {}).get("id") == scenario_id, tries=30)

    time.sleep(3)                                # let the viewer finish loading
    run = _post("/api/simulations/run")
    if not run.get("ok"):
        sys.exit(f"run rejected: {run.get('error')}\n{STALE_HELP}")
    run_id = run["simulation"]["id"]
    final = _wait(lambda: _get("/api/simulations/latest"),
                  lambda v: (v.get("simulation") or {}).get("id") == run_id
                  and v["simulation"]["status"] in
                  ("completed", "failed", "rejected"))
    status = final["simulation"]["status"]
    if status != "completed":
        sys.exit(f"simulation {status}: {final['simulation'].get('detail')}. "
                 "Reload the viewer tab and retry (one run per fresh session).")

    # The archive store keeps the previous run's files until the new archive
    # lands, so wait for one tagged with this run and verify the bytes.
    listing = _wait(lambda: _get("/api/results/latest/sqlite"),
                    lambda v: v.get("simulation_id") == run_id
                    and bool(v.get("sqlite_files")), tries=60)
    sqlite_file = listing["sqlite_files"][0]
    raw = _get_bytes(f"/api/results/latest/files/{sqlite_file['index']}")
    if hashlib.sha256(raw).hexdigest() != sqlite_file["sha256"]:
        sys.exit("downloaded result file does not match the published archive; "
                 "the archive changed mid-download. Retry.")
    out = pathlib.Path(tempfile.gettempdir()) / f"bottleneck_{n_agents}.sqlite"
    out.write_bytes(raw)
    return out


# --- analysis (pedpy) ------------------------------------------------------

def analyse(sqlite_path: pathlib.Path, n_expected: int) -> None:
    import numpy as np
    import matplotlib.pyplot as plt
    from pedpy import (load_trajectory_from_jupedsim_sqlite,
                       MeasurementLine, compute_n_t)

    traj = load_trajectory_from_jupedsim_sqlite(sqlite_path)
    n_agents = traj.data["id"].nunique()
    if n_agents != n_expected:
        sys.exit(f"freshness check failed: got {n_agents} agents, "
                 f"expected {n_expected} — stale or misconfigured run.")

    # Evacuation curve from agent removal: each agent's last frame = exit time.
    exit_time = np.sort(
        traj.data.groupby("id")["frame"].max().values) / traj.frame_rate
    evacuated = np.arange(1, n_agents + 1)

    # Independent cross-check: flow across a full-height line inside the throat.
    line = MeasurementLine([[MEASURE_X, 0.0], [MEASURE_X, 5.0]])
    n_t, _ = compute_n_t(traj_data=traj, measurement_line=line)
    crossings = int(n_t["cumulative_pedestrians"].max())

    # Steady-state flow: slope of N(t) over the congested 10-90% band.
    lo, hi = int(0.1 * n_agents), int(0.9 * n_agents)
    flow = float(np.polyfit(exit_time[lo:hi], evacuated[lo:hi], 1)[0])
    specific_flow = flow / DOOR_WIDTH_M

    # CSV: the evacuation curve.
    csv = OUT_DIR / "bottleneck_flow.csv"
    csv.write_text("exit_time_s,cumulative_evacuated\n" + "".join(
        f"{t:.3f},{n}\n" for t, n in zip(exit_time, evacuated)))

    # Figure.
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.step(exit_time, evacuated, where="post", color="#2563eb", lw=2,
            label="evacuated N(t)")
    band = slice(lo, hi)
    fit = np.poly1d(np.polyfit(exit_time[band], evacuated[band], 1))
    ax.plot(exit_time[band], fit(exit_time[band]), "--", color="#dc2626",
            lw=1.8, label=f"steady flow {flow:.2f} ped/s")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("agents evacuated")
    ax.set_title(f"Door-bottleneck evacuation — {n_agents} agents, "
                 f"{DOOR_WIDTH_M:.1f} m exit")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    png = OUT_DIR / "bottleneck_evacuation.png"
    fig.savefig(png, dpi=130)

    print(f"agents            : {n_agents}")
    print(f"total evacuation  : {exit_time[-1]:.1f} s")
    print(f"line crossings    : {crossings} (cross-check vs {n_agents})")
    print(f"steady-state flow : {flow:.2f} ped/s")
    print(f"specific flow     : {specific_flow:.2f} ped/(s*m)  [W={DOOR_WIDTH_M} m]")
    print(f"wrote {png.name} and {csv.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true",
                       help="drive a fresh simulation, then analyse")
    group.add_argument("--sqlite", type=pathlib.Path,
                       help="analyse an existing JuPedSim SQLite file")
    parser.add_argument("--agents", type=int, default=N_AGENTS)
    args = parser.parse_args()

    sqlite_path = run_bottleneck(args.agents) if args.run else args.sqlite
    analyse(sqlite_path, args.agents)


if __name__ == "__main__":
    main()
