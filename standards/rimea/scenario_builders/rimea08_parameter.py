"""
RiMEA Test 8 – Parameter Analysis
==================================
Scenario builder for a 3-floor building:
  - N_FLOORS floors, each with N_ROOMS_SIDE rooms on BOTH sides of a central corridor
  - Rooms are 3 m × 3 m, door width 1 m, corridor 2 m wide
  - Floors are connected by a 2 m-wide staircase on the right end
  - Single exit at the right end of the ground-floor corridor
  - 4 agents per room → 3 × 5 × 2 × 4 = 120 agents total

Exports
-------
build_geometry()          → (shapely.Polygon, list[shapely.Polygon])
build_geometry_wkt()      → str
build_raw_scenario(v0)    → dict suitable for jupedsim-scenarios/web import
build_simulation(v0_arr)  → (jps.Simulation, exit_stage_id)
run_until_evacuation(sim) → (evac_time_s, n_evacuated)
v0_constant(v0)           → np.ndarray   shape (N_AGENTS,)
v0_uniform(mean, half_range, seed) → np.ndarray
N_AGENTS                  : int  (120)
"""

from shapely.geometry import box as sbox
from shapely.ops import unary_union
import numpy as np
import jupedsim as jps

# ── Geometry constants ──────────────────────────────────────────────────────
ROOM_W          = 3.0   # room width  [m]
ROOM_D          = 3.0   # room depth  [m]
CORRIDOR_W      = 2.0   # corridor width [m]
DOOR_W          = 1.0   # door-wing width [m]
WALL_T          = 0.12  # wall thickness [m]
N_ROOMS_SIDE    = 5     # rooms per side per floor
N_FLOORS        = 3
STAIR_W         = 2.0   # staircase width [m]
STAIR_H         = 3.0   # staircase height in 2-D plane [m]
PERSONS_PER_ROOM = 4
AGENT_RADIUS    = 0.15  # [m]

FLOOR_W      = N_ROOMS_SIDE * ROOM_W          # 15 m
FLOOR_H      = 2 * ROOM_D + CORRIDOR_W        #  8 m
FLOOR_STRIDE = FLOOR_H + STAIR_H              # 11 m  (y-distance between floor origins)

N_AGENTS = N_FLOORS * N_ROOMS_SIDE * 2 * PERSONS_PER_ROOM   # 120

JOURNEY_ID = "jps-journeys_0"
EXIT_ID = "jps-exits_0"


# ── Geometry builder ────────────────────────────────────────────────────────

def _floor_geometry(y0: float):
    """
    Build the walkable polygon and room distribution polygons for one floor.

    Rooms open into the central corridor through 1 m doors.
    Inter-room walls and corridor-room walls are modelled as thin obstacle
    rectangles subtracted from the full floor rectangle.

    Parameters
    ----------
    y0 : float
        Bottom y-coordinate of this floor.

    Returns
    -------
    walkable   : shapely.Polygon
    room_polys : list[shapely.Polygon]  – one per room, safe for agent distribution
    """
    full_floor = sbox(0, y0, FLOOR_W, y0 + FLOOR_H)

    obstacles  = []
    room_polys = []

    for i in range(N_ROOMS_SIDE):
        rx = i * ROOM_W
        door_lo = rx + (ROOM_W - DOOR_W) / 2.0
        door_hi = door_lo + DOOR_W

        # ── bottom room (y0 … y0+ROOM_D) ──────────────────────────────────
        room_polys.append(sbox(rx + 0.35, y0 + 0.35,
                               rx + ROOM_W - 0.35, y0 + ROOM_D - 0.35))

        # horizontal wall: bottom-room ceiling → corridor (door gap removed)
        wall_y = y0 + ROOM_D
        if door_lo > rx:
            obstacles.append(sbox(rx, wall_y - WALL_T, door_lo, wall_y))
        if door_hi < rx + ROOM_W:
            obstacles.append(sbox(door_hi, wall_y - WALL_T, rx + ROOM_W, wall_y))

        # ── top room (y0+ROOM_D+CORRIDOR_W … y0+FLOOR_H) ─────────────────
        top_y = y0 + ROOM_D + CORRIDOR_W
        room_polys.append(sbox(rx + 0.35, top_y + 0.35,
                               rx + ROOM_W - 0.35, top_y + ROOM_D - 0.35))

        # horizontal wall: corridor → top-room floor (door gap removed)
        if door_lo > rx:
            obstacles.append(sbox(rx, top_y, door_lo, top_y + WALL_T))
        if door_hi < rx + ROOM_W:
            obstacles.append(sbox(door_hi, top_y, rx + ROOM_W, top_y + WALL_T))

        # ── vertical walls between adjacent rooms ─────────────────────────
        if i < N_ROOMS_SIDE - 1:
            vx = rx + ROOM_W
            # bottom row
            obstacles.append(sbox(vx - WALL_T / 2, y0,
                                   vx + WALL_T / 2, y0 + ROOM_D))
            # top row
            obstacles.append(sbox(vx - WALL_T / 2, top_y,
                                   vx + WALL_T / 2, top_y + ROOM_D))

    walkable = full_floor.difference(unary_union(obstacles))
    return walkable, room_polys


def build_geometry():
    """
    Assemble the full 3-floor building geometry.

    Floor layout (y increases upward in the 2-D plane):
        Floor 0  (ground): y = 0           … FLOOR_H
        Stair  0→1:        y = FLOOR_H     … FLOOR_H + STAIR_H
        Floor 1:           y = FLOOR_STRIDE … FLOOR_STRIDE + FLOOR_H
        Stair  1→2:        …
        Floor 2 (top):     y = 2*FLOOR_STRIDE … 2*FLOOR_STRIDE + FLOOR_H

    Staircase: x = FLOOR_W - STAIR_W … FLOOR_W  (right side of each floor)
    Exit:      right end of ground-floor corridor

    Returns
    -------
    geometry   : shapely.Polygon   (the full walkable area)
    room_polys : list[shapely.Polygon]
    """
    parts      = []
    all_rooms  = []

    for f in range(N_FLOORS):
        y0 = f * FLOOR_STRIDE
        walkable, rooms = _floor_geometry(y0)
        parts.append(walkable)
        all_rooms.extend(rooms)

        # staircase connecting this floor to the next
        if f < N_FLOORS - 1:
            stair_x0 = FLOOR_W - STAIR_W
            stair_y0 = y0 + FLOOR_H
            parts.append(sbox(stair_x0, stair_y0, FLOOR_W, stair_y0 + STAIR_H))

    geometry = unary_union(parts)
    return geometry, all_rooms


def build_geometry_wkt() -> str:
    """Return the web-scenario walkable geometry as WKT."""
    geometry, _ = build_geometry()
    return geometry.wkt


def build_raw_scenario(
    v0: float = 1.25,
    seed: int = 42,
    max_simulation_time: float = 600.0,
) -> dict:
    """Build a representative web-app scenario for RiMEA Test 8.

    The notebook varies desired speeds through the direct JuPedSim API. The
    exported ZIP uses the central reference value, 1.25 m/s, and retains the
    same 30 room distributions with four agents per room.
    """
    _, room_polys = build_geometry()

    distributions = {}
    for index, room in enumerate(room_polys):
        dist_id = f"jps-distributions_{index}"
        distributions[dist_id] = {
            "type": "polygon",
            "coordinates": [list(point) for point in room.exterior.coords],
            "parameters": {
                "number": PERSONS_PER_ROOM,
                "radius": AGENT_RADIUS,
                "v0": float(v0),
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
                "use_flow_spawning": False,
            },
            "journey_weights": [{"journey_id": JOURNEY_ID, "weight": 100}],
        }

    corridor_y0 = ROOM_D
    corridor_y1 = ROOM_D + CORRIDOR_W
    return {
        "config": {
            "simulation_settings": {
                "baseSeed": seed,
                "simulationParams": {
                    "model_type": "CollisionFreeSpeedModel",
                    "max_simulation_time": max_simulation_time,
                },
            }
        },
        "distributions": distributions,
        "exits": {
            EXIT_ID: {
                "type": "polygon",
                "coordinates": [
                    [FLOOR_W - 0.35, corridor_y0 + 0.1],
                    [FLOOR_W - 0.05, corridor_y0 + 0.1],
                    [FLOOR_W - 0.05, corridor_y1 - 0.1],
                    [FLOOR_W - 0.35, corridor_y1 - 0.1],
                    [FLOOR_W - 0.35, corridor_y0 + 0.1],
                ],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "zones": {},
        "journeys_v2": [
            {
                "id": JOURNEY_ID,
                "name": JOURNEY_ID,
                "color": "#3b82f6",
                "sequence": [EXIT_ID],
            }
        ],
    }


# ── Simulation builder ──────────────────────────────────────────────────────

def build_simulation(v0_array, seed: int = 42):
    """
    Build a jps.Simulation populated with 120 agents whose desired speeds
    are given by v0_array.

    Parameters
    ----------
    v0_array : array-like, length == N_AGENTS (120)
        Desired speed [m/s] for each agent in room-iteration order.
    seed : int
        Master RNG seed for spatial distribution of agents.

    Returns
    -------
    simulation : jps.Simulation
    exit_id    : int   (stage id of the single exit)
    """
    assert len(v0_array) == N_AGENTS, \
        f"Expected {N_AGENTS} v0 values, got {len(v0_array)}"

    geometry, room_polys = build_geometry()

    simulation = jps.Simulation(
        model=jps.CollisionFreeSpeedModel(
            strength_neighbor_repulsion=2.6,
            range_neighbor_repulsion=0.1,
        ),
        geometry=geometry,
        dt=0.05,
    )

    # Exit: thin strip at the right end of the ground-floor corridor
    corr_y0 = ROOM_D                      # 3.0
    corr_y1 = ROOM_D + CORRIDOR_W         # 5.0
    exit_poly = sbox(FLOOR_W - 0.35, corr_y0 + 0.1,
                     FLOOR_W - 0.05, corr_y1 - 0.1)
    exit_id = simulation.add_exit_stage(list(exit_poly.exterior.coords[:-1]))

    journey    = jps.JourneyDescription([exit_id])
    journey_id = simulation.add_journey(journey)

    rng      = np.random.default_rng(seed)
    v0_iter  = iter(v0_array)

    for room_poly in room_polys:
        positions = jps.distributions.distribute_by_number(
            polygon=room_poly,
            number_of_agents=PERSONS_PER_ROOM,
            distance_to_agents=0.45,
            distance_to_polygon=0.15,
            seed=int(rng.integers(0, 2**31)),
        )
        for pos in positions:
            simulation.add_agent(
                jps.CollisionFreeSpeedModelAgentParameters(
                    journey_id=journey_id,
                    stage_id=exit_id,
                    position=pos,
                    desired_speed=float(next(v0_iter)),
                    radius=AGENT_RADIUS,
                )
            )

    return simulation, exit_id


# ── Run helper ──────────────────────────────────────────────────────────────

def run_until_evacuation(simulation, max_time: float = 600.0):
    """
    Iterate simulation until all agents have evacuated or max_time is reached.

    Returns
    -------
    evac_time    : float   simulated seconds elapsed
    n_evacuated  : int     number of agents that left through the exit
    """
    dt     = 0.05
    max_it = int(max_time / dt)
    it     = 0
    while simulation.agent_count() > 0 and it < max_it:
        simulation.iterate()
        it += 1
    n_evacuated = N_AGENTS - simulation.agent_count()
    return it * dt, n_evacuated


# ── Speed-array helpers ─────────────────────────────────────────────────────

def v0_constant(v0: float) -> np.ndarray:
    """All agents get the same desired speed."""
    return np.full(N_AGENTS, float(v0))


def v0_uniform(mean: float, half_range: float, seed: int = 0) -> np.ndarray:
    """
    Uniformly distributed desired speeds centred on *mean*.

    Example: v0_uniform(0.75, 0.25) → U(0.5, 1.0)
    """
    rng = np.random.default_rng(seed)
    return rng.uniform(mean - half_range, mean + half_range, N_AGENTS)
