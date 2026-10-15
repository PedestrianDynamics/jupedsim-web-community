"""Overtaking people with movement disabilities: ISO 20414 Test 7, NIST Verif.2.10.

Both standards use the same layout (ISO 20414:2020 Table 8 and Figure 4;
NIST TN 1822 Verif.2.10 and Figure 7) and differ only in the ramp length:
12 m in ISO, 2 m in NIST.

- Room 1: 5 m x 4 m. Zone 1 holds 24 occupants at 1.25 m/s with the model's
  default body size. Zone 2 is the last 1 m of room 1 at the ramp entrance and
  holds one occupant.
- Ramp: 1.5 m wide, centred on the rooms.
- Room 2: 5 m x 4 m, with a 1 m exit opening centred on its far wall. The
  opening leads into a short doorway; agents leave the simulation there.

Scenario 1: the zone-2 occupant is disabled. It walks at 0.8 m/s on
horizontal surfaces and 0.4 m/s on the ramp, and occupies more than half the
ramp width (0.75 m). Scenario 2: the zone-2 occupant is like the others.

jupedsim-scenarios 0.7.3 applies zone and checkpoint speed factors to every
agent inside the polygon, so it cannot slow one occupant on the ramp alone.
This module therefore drives jupedsim directly and sets the disabled
occupant's desired speed every step from its position. The other occupants
keep 1.25 m/s throughout, as the standards specify.
"""

from __future__ import annotations

from dataclasses import dataclass

import jupedsim as jps
import pandas as pd
from shapely.geometry import Polygon, box

ROOM_LENGTH = 5.0
ROOM_WIDTH = 4.0
RAMP_WIDTH = 1.5
ZONE2_LENGTH = 1.0
EXIT_WIDTH = 1.0
DOORWAY_DEPTH = 0.5

N_ZONE1 = 24
SPEED = 1.25
DEFAULT_RADIUS = 0.2
DISABLED_SPEED_FLAT = 0.8
DISABLED_SPEED_RAMP = 0.4
DISABLED_RADIUS = 0.4  # 0.8 m wide, more than half the 1.5 m ramp

RAMP_LENGTH = {"iso": 12.0, "nist": 2.0}

DT = 0.01
MAX_TIME = 300.0
RECORD_EVERY = 10  # steps; 0.1 s


@dataclass
class Layout:
    ramp_length: float

    @property
    def ramp_x(self) -> tuple[float, float]:
        return ROOM_LENGTH, ROOM_LENGTH + self.ramp_length

    @property
    def total_length(self) -> float:
        return 2 * ROOM_LENGTH + self.ramp_length

    @property
    def ramp(self) -> Polygon:
        y0 = (ROOM_WIDTH - RAMP_WIDTH) / 2
        return box(*self.ramp_x[:1], y0, self.ramp_x[1], y0 + RAMP_WIDTH)

    @property
    def doorway(self) -> Polygon:
        y0 = (ROOM_WIDTH - EXIT_WIDTH) / 2
        x0 = self.total_length
        return box(x0, y0, x0 + DOORWAY_DEPTH, y0 + EXIT_WIDTH)

    @property
    def walkable(self) -> Polygon:
        room1 = box(0, 0, ROOM_LENGTH, ROOM_WIDTH)
        room2 = box(self.ramp_x[1], 0, self.total_length, ROOM_WIDTH)
        return room1.union(self.ramp).union(room2).union(self.doorway)

    @property
    def zone1(self) -> Polygon:
        return box(0, 0, ROOM_LENGTH - ZONE2_LENGTH, ROOM_WIDTH)

    @property
    def zone2_position(self) -> tuple[float, float]:
        return ROOM_LENGTH - ZONE2_LENGTH / 2, ROOM_WIDTH / 2

    @property
    def exit(self) -> Polygon:
        # An agent leaves once its centre passes the door line.
        return self.doorway


@dataclass
class Result:
    layout: Layout
    disabled: bool
    zone2_id: int
    zone1_ids: list[int]
    exit_times: dict[int, float]
    trajectory: pd.DataFrame  # columns: id, time, x, y

    def zone1_exit_times(self) -> list[float]:
        return [self.exit_times[i] for i in self.zone1_ids]


def run(standard: str, disabled: bool, seed: int = 42) -> Result:
    """Run one scenario; standard is "iso" (12 m ramp) or "nist" (2 m ramp)."""
    layout = Layout(RAMP_LENGTH[standard])
    simulation = jps.Simulation(
        model=jps.CollisionFreeSpeedModel(
            strength_neighbor_repulsion=2.6, range_neighbor_repulsion=0.1
        ),
        geometry=layout.walkable,
        dt=DT,
    )
    exit_id = simulation.add_exit_stage(list(layout.exit.exterior.coords)[:-1])
    journey_id = simulation.add_journey(jps.JourneyDescription([exit_id]))

    def add(position, speed, radius):
        return simulation.add_agent(
            jps.CollisionFreeSpeedModelAgentParameters(
                journey_id=journey_id,
                stage_id=exit_id,
                position=position,
                desired_speed=speed,
                radius=radius,
            )
        )

    positions = jps.distributions.distribute_by_number(
        polygon=layout.zone1,
        number_of_agents=N_ZONE1,
        distance_to_agents=2 * DEFAULT_RADIUS,
        distance_to_polygon=DEFAULT_RADIUS,
        seed=seed,
    )
    zone1_ids = [add(p, SPEED, DEFAULT_RADIUS) for p in positions]
    if disabled:
        zone2_id = add(layout.zone2_position, DISABLED_SPEED_FLAT, DISABLED_RADIUS)
    else:
        zone2_id = add(layout.zone2_position, SPEED, DEFAULT_RADIUS)

    ramp_x0, ramp_x1 = layout.ramp_x
    exit_times: dict[int, float] = {}
    rows = []
    live = set(zone1_ids) | {zone2_id}
    step = 0
    while live and simulation.elapsed_time() < MAX_TIME:
        if disabled and zone2_id in live:
            agent = simulation.agent(zone2_id)
            on_ramp = ramp_x0 <= agent.position[0] <= ramp_x1
            agent.model.desired_speed = (
                DISABLED_SPEED_RAMP if on_ramp else DISABLED_SPEED_FLAT
            )
        simulation.iterate()
        step += 1
        present = {a.id for a in simulation.agents()}
        for agent_id in live - present:
            exit_times[agent_id] = simulation.elapsed_time()
        live &= present
        if step % RECORD_EVERY == 0:
            t = simulation.elapsed_time()
            rows.extend(
                (a.id, t, a.position[0], a.position[1]) for a in simulation.agents()
            )

    return Result(
        layout=layout,
        disabled=disabled,
        zone2_id=zone2_id,
        zone1_ids=zone1_ids,
        exit_times=exit_times,
        trajectory=pd.DataFrame(rows, columns=["id", "time", "x", "y"]),
    )
