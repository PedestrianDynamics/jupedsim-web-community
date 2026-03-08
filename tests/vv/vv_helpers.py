"""Shared helpers for V&V tests — standalone version.

Uses JuPedSim directly (no web-app backend) + pedpy for trajectory I/O.
"""

import pathlib
import tempfile

import pytest
import shapely
from shapely import wkt as shapely_wkt

try:
    import jupedsim as jps
    import pedpy

    HAS_JUPEDSIM = True
except ImportError:
    HAS_JUPEDSIM = False


def _make_model(model_type: str, **kwargs):
    """Create a JuPedSim model instance."""
    models = {
        "CollisionFreeSpeedModel": jps.CollisionFreeSpeedModel,
        "CollisionFreeSpeedModelV2": jps.CollisionFreeSpeedModelV2,
        "GeneralizedCentrifugalForceModel": jps.GeneralizedCentrifugalForceModel,
        "SocialForceModel": jps.SocialForceModel,
        "AnticipationVelocityModel": jps.AnticipationVelocityModel,
    }
    cls = models.get(model_type)
    if cls is None:
        raise ValueError(f"Unknown model type: {model_type}")
    return cls()


def _normalize_polygon(wkt_string: str) -> shapely.Polygon:
    """Parse WKT and ensure valid, oriented polygon."""
    geom = shapely_wkt.loads(wkt_string)
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    if not geom.is_valid:
        geom = geom.buffer(0)
    if not geom.exterior.is_ccw:
        geom = shapely.Polygon(
            geom.exterior.coords[::-1],
            [r.coords[::-1] for r in geom.interiors],
        )
    return geom


def _parse_exit_polygon(exit_def: dict) -> shapely.Polygon:
    """Build a Shapely polygon from exit coordinates."""
    coords = exit_def["coordinates"]
    return shapely.Polygon(coords)


def _distribute_agents(dist_def: dict, seed: int) -> list[tuple[float, float]]:
    """Distribute agents inside a polygon using JuPedSim's built-in distributor."""
    coords = dist_def["coordinates"]
    polygon = shapely.Polygon(coords)
    params = dist_def.get("parameters", {})
    n_agents = params.get("number", 10)
    radius = params.get("radius", 0.15)
    return jps.distributions.distribute_by_number(
        polygon=polygon,
        number_of_agents=n_agents,
        distance_to_agents=2 * radius,
        distance_to_polygon=radius + 0.05,
        seed=seed,
    )


def run_vv_scenario(
    walkable_area_wkt: str,
    exits: dict,
    distributions: dict,
    model_type: str = "CollisionFreeSpeedModel",
    max_simulation_time: float = 300.0,
    seed: int = 42,
    checkpoints: dict | None = None,
    journeys: list | None = None,
    transitions: list | None = None,
    model_params: dict | None = None,
) -> tuple[dict, "pedpy.TrajectoryData"]:
    """Run a V&V scenario and return (metrics, trajectory).

    This is the standalone equivalent of the web-app's simulation runner.
    Uses JuPedSim directly and reads results with pedpy.
    """
    if not HAS_JUPEDSIM:
        pytest.skip("JuPedSim not installed")

    geometry = _normalize_polygon(walkable_area_wkt)
    model = _make_model(model_type)

    # SQLite output
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    output_path = pathlib.Path(tmp.name)
    tmp.close()

    writer = jps.SqliteTrajectoryWriter(
        output_file=output_path, every_nth_frame=10
    )

    sim = jps.Simulation(
        model=model,
        geometry=geometry,
        trajectory_writer=writer,
    )

    # --- Set up exits as stages ---
    exit_stage_ids = {}
    for ek, edef in exits.items():
        poly = _parse_exit_polygon(edef)
        stage_id = sim.add_exit_stage(poly)
        exit_stage_ids[ek] = stage_id

    # --- Build journeys ---
    # Default: each distribution → first exit
    if journeys is None:
        dist_keys = list(distributions.keys())
        exit_keys = list(exits.keys())
        # Single journey: all distributions → first exit
        journey_map = {}
        for dk in dist_keys:
            ek = exit_keys[0]
            journey_map[dk] = ek
        # One journey per unique exit target
        jd = jps.JourneyDescription()
        ek = exit_keys[0]
        jd.add(exit_stage_ids[ek])
        jid = sim.add_journey(jd)
        dist_journey_stage = {
            dk: (jid, exit_stage_ids[ek]) for dk in dist_keys
        }
    else:
        # Explicit journeys: each journey has stages [dist_key, exit_key, ...]
        dist_journey_stage = {}
        for j_def in journeys:
            jd = jps.JourneyDescription()
            stages_in_journey = j_def["stages"]
            # Collect only exit/checkpoint stage IDs (skip distribution keys)
            stage_ids_for_journey = []
            for s in stages_in_journey:
                if s in exit_stage_ids:
                    stage_ids_for_journey.append(exit_stage_ids[s])
            for sid in stage_ids_for_journey:
                jd.add(sid)
            jid = sim.add_journey(jd)
            # Map distributions in this journey to (journey_id, first_stage_id)
            first_stage = stage_ids_for_journey[0] if stage_ids_for_journey else 0
            for s in stages_in_journey:
                if s in distributions:
                    dist_journey_stage[s] = (jid, first_stage)

    # --- Add agents ---
    total_agents = 0
    for dk, ddef in distributions.items():
        positions = _distribute_agents(ddef, seed)
        params = ddef.get("parameters", {})
        v0 = params.get("v0", 1.2)
        radius = params.get("radius", 0.15)
        jid, sid = dist_journey_stage.get(dk, (0, 0))
        for pos in positions:
            agent_params = jps.CollisionFreeSpeedModelAgentParameters(
                journey_id=jid,
                stage_id=sid,
                position=pos,
                desired_speed=v0,
                radius=radius,
            )
            sim.add_agent(agent_params)
            total_agents += 1

    # --- Run simulation ---
    dt = sim.delta_time()
    max_iterations = int(max_simulation_time / dt)
    for _ in range(max_iterations):
        if sim.agent_count() == 0:
            break
        sim.iterate()

    remaining = sim.agent_count()
    evac_time = round(sim.elapsed_time(), 2)

    metrics = {
        "success": True,
        "evacuation_time": evac_time,
        "total_agents": total_agents,
        "agents_evacuated": total_agents - remaining,
        "agents_remaining": remaining,
    }

    # Read trajectory with pedpy (may be empty if agents evacuate before first write)
    try:
        trajectory = pedpy.load_trajectory_from_jupedsim_sqlite(output_path)
    except Exception:
        trajectory = None

    # Clean up
    try:
        output_path.unlink()
    except OSError:
        pass

    return metrics, trajectory


def measure_flow_rate(metrics: dict) -> float:
    """Calculate flow rate as agents_evacuated / evacuation_time."""
    evac_time = metrics.get("evacuation_time", 0)
    if evac_time <= 0:
        return 0.0
    return metrics["agents_evacuated"] / evac_time


def agents_within_bounds(
    trajectory: "pedpy.TrajectoryData",
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    margin: float = 0.5,
) -> list[str]:
    """Check all agents stay within bounding box. Returns violation descriptions.

    Works with pedpy TrajectoryData (DataFrame with id, frame, x, y columns).
    """
    violations = []
    if trajectory is None:
        return violations
    df = trajectory.data
    oob = df[
        (df["x"] < min_x - margin)
        | (df["x"] > max_x + margin)
        | (df["y"] < min_y - margin)
        | (df["y"] > max_y + margin)
    ]
    for _, row in oob.iterrows():
        violations.append(
            f"Frame {int(row['frame'])}, agent {int(row['id'])}: "
            f"({row['x']:.4f}, {row['y']:.4f}) outside "
            f"[{min_x}, {max_x}] x [{min_y}, {max_y}]"
        )
    return violations
