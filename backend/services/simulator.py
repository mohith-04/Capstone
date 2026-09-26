"""
services/simulator.py — Ambulance movement simulator
=====================================================

Simulates an ambulance driving along a pre-computed route, emitting GPS-like
telemetry pings at regular intervals.  Each ping contains (lat, lng, speed,
heading, distance_remaining, eta, timestamp) — the same fields a real GPS
tracker would produce.

Architecture
------------
Each simulation is an `AmbulanceSimulation` instance that holds:
  - The route (list of (lat, lng) coordinate pairs from Stage 2)
  - The segment metadata (speeds, congestion factors, etc.)
  - A cursor tracking the ambulance's position along the polyline
  - An asyncio.Event for cancellation

The `tick()` method advances the cursor by `dt × current_speed` metres and
returns a TelemetryPing.  The WebSocket handler in routers/simulation.py
calls `tick()` in a loop with `asyncio.sleep(dt)` between iterations.

Movement model (key viva talking point)
-----------------------------------------
The ambulance does NOT teleport node-to-node.  It moves continuously along
each edge at the edge's speed (adjusted for congestion).  Between two nodes
the position is linearly interpolated — this produces smooth motion on the
Leaflet map and realistic ETA updates.

For an ambulance with emergency priority, we assume:
  - It can drive at ~1.3x the road's nominal speed (sirens + right-of-way)
  - Signal delay is skipped when priority preemption is active (Stage 4)
  - Congestion factor is reduced to sqrt(factor) — traffic parts, but not
    perfectly, so an ambulance in 4x congestion effectively sees ~2x delay

Rerouting
---------
When the route API is called with new congestion, the simulation can be
given a new route via `reroute()`.  This splices the new path at the
ambulance's current position — the ambulance never teleports backward.

Thread safety
-------------
All state is per-instance.  The global `_simulations` dict is accessed only
from the async event loop (no threads), so no locking is needed.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telemetry data model
# ---------------------------------------------------------------------------

class SimulationState(str, Enum):
    WAITING   = "waiting"     # Created but not yet started
    RUNNING   = "running"     # Ambulance is moving
    REROUTING = "rerouting"   # Mid-reroute (brief transient state)
    ARRIVED   = "arrived"     # Reached destination
    CANCELLED = "cancelled"   # Manually stopped


@dataclass
class TelemetryPing:
    """
    One GPS-like position report from the ambulance.

    These fields mirror what a real vehicle tracker (e.g. Teltonika FMB920)
    would produce over its protocol.  The simulation generates them
    synthetically so the frontend doesn't know (or care) whether the data
    is real or simulated.
    """
    ambulance_id: str
    lat: float
    lng: float
    speed_kmh: float          # current ground speed
    heading_deg: float        # bearing 0–360, 0 = north
    distance_remaining_m: float
    distance_covered_m: float
    eta_seconds: float        # estimated time to destination
    current_segment_index: int
    total_segments: int
    state: str                # SimulationState value
    timestamp: float          # Unix epoch seconds
    # Context for the frontend sidebar
    current_road_name: str
    congestion_factor: float  # on the current segment
    is_near_signal: bool      # approaching a signal node?
    signal_node_id: Optional[int] = None
    reroute_count: int = 0


# ---------------------------------------------------------------------------
# Ambulance speed model
# ---------------------------------------------------------------------------

# Ambulances with sirens typically drive 20–30% faster than ambient traffic.
# Source: NHTSA Emergency Vehicle Operator Course guidelines.
AMBULANCE_SPEED_FACTOR: float = 1.3

# In congestion, traffic partially clears for ambulances.  We model this as
# effective_congestion = sqrt(raw_congestion).
# So 4x raw congestion → 2x effective delay, 9x → 3x.
# This is a simplification of the "Moses parting the Red Sea" effect.
def effective_congestion(raw: float) -> float:
    return max(1.0, math.sqrt(raw))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees (0 = north, 90 = east)."""
    dlon = math.radians(lon2 - lon1)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _interpolate(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float):
    """
    Linear interpolation between two (lat, lon) points.
    fraction=0 → (lat1, lon1), fraction=1 → (lat2, lon2).

    For the short distances involved (< 2 km per edge in a city graph),
    linear interpolation in lat/lon space is accurate to within ~1 metre.
    Full spherical interpolation (SLERP) is unnecessary.
    """
    return (
        lat1 + (lat2 - lat1) * fraction,
        lon1 + (lon2 - lon1) * fraction,
    )


# ---------------------------------------------------------------------------
# Precomputed segment info (for fast lookups during simulation)
# ---------------------------------------------------------------------------

@dataclass
class SimSegment:
    """One edge of the route, with precomputed geometry and speed."""
    u: int                   # origin node
    v: int                   # destination node
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    length_m: float          # precomputed haversine distance
    speed_kmh: float         # nominal road speed (before ambulance factor)
    congestion_factor: float
    road_name: str
    is_signal_at_dest: bool
    signal_node_id: Optional[int]   # v if is_signal_at_dest, else None


def _build_segments(coords: list[tuple[float, float]], segments_data: list[dict]) -> list[SimSegment]:
    """
    Convert the route API response into SimSegment objects.

    `coords` is the list of [lat, lng] pairs (one per graph node).
    `segments_data` is the per-edge metadata from the routing result.
    There are len(coords)-1 edges, matching len(segments_data).
    """
    result = []
    for i, seg_data in enumerate(segments_data):
        lat1, lng1 = coords[i]
        lat2, lng2 = coords[i + 1]
        dist = _haversine_m(lat1, lng1, lat2, lng2)
        if dist < 0.1:
            dist = 0.1  # avoid division by zero on near-zero-length edges
        result.append(SimSegment(
            u=seg_data["u"],
            v=seg_data["v"],
            start_lat=lat1,
            start_lng=lng1,
            end_lat=lat2,
            end_lng=lng2,
            length_m=dist,
            speed_kmh=seg_data["speed_kph"],
            congestion_factor=seg_data["congestion_factor"],
            road_name=seg_data.get("name", ""),
            is_signal_at_dest=seg_data.get("is_signal_at_dest", False),
            signal_node_id=seg_data["v"] if seg_data.get("is_signal_at_dest", False) else None,
        ))
    return result


# ---------------------------------------------------------------------------
# AmbulanceSimulation — the core simulation engine
# ---------------------------------------------------------------------------

class AmbulanceSimulation:
    """
    Stateful simulation of one ambulance traversal.

    Usage:
        sim = AmbulanceSimulation("AMB-001", route_response)
        sim.start()
        while not sim.is_finished:
            ping = sim.tick(dt=1.0)
            # send ping over WebSocket
            await asyncio.sleep(1.0)
    """

    def __init__(
        self,
        ambulance_id: str,
        route_data: dict,
        speed_multiplier: float = 1.0,
        severity: str = "critical",
    ):
        self.ambulance_id = ambulance_id
        self.severity = severity
        self.speed_multiplier = speed_multiplier  # extra factor for demo speed-up

        # Parse route data.
        #
        # Two coordinate arrays come from the backend:
        #   coords      — geometry-expanded (lots of intermediate curve points).
        #                 Used ONLY for the map polyline display.
        #   node_coords — one (lat, lng) per graph NODE, exactly len(segments)+1 entries.
        #                 Used for simulator movement (_build_segments).
        #
        # CRITICAL: _build_segments must use node_coords, NOT coords.
        # coords has many more entries than segments (e.g. 251 coords vs 86 segments).
        # If we passed coords to _build_segments, it would only iterate over the first
        # len(segments) entries (86 of 251), think the route is done, and trigger
        # ARRIVED halfway through — exactly the "teleport" bug the user reported.
        self.coords: list[tuple[float, float]] = [tuple(c) for c in route_data["coords"]]

        # node_coords: the node-level coords used for movement simulation.
        # Falls back to coords if node_coords is absent (older route_data format).
        raw_node_coords = route_data.get("node_coords") or route_data["coords"]
        self._node_coords: list[tuple[float, float]] = [tuple(c) for c in raw_node_coords]

        self.node_path: list[int] = route_data["node_path"]
        self.segments: list[SimSegment] = _build_segments(self._node_coords, route_data["segments"])
        self.signal_nodes_on_route: list[int] = route_data.get("signal_nodes_on_route", [])

        # Cursor state
        self._seg_idx: int = 0           # current segment index
        self._seg_progress_m: float = 0.0  # metres travelled within current segment

        # Accumulators
        self._total_distance_m: float = sum(s.length_m for s in self.segments)
        self._distance_covered_m: float = 0.0

        # State
        self.state: SimulationState = SimulationState.WAITING
        self.reroute_count: int = 0
        self._cancel_event: asyncio.Event = asyncio.Event()

        # Stage 7: Track actual wall-clock elapsed time for before/after metrics.
        # We record started_at when start() is called (not __init__) so the
        # timer doesn't include HTTP round-trip or WS handshake time.
        self.started_at: float = 0.0
        self.finished_at: float = 0.0

        # The backend router's A*-estimated travel time (before demo speed-up).
        # This is the "ground-truth physics" ETA — what the route engine predicted.
        # We expose it in the arrived message so the frontend can show:
        #   Predicted time (A*) vs Actual elapsed vs "Without system" baseline.
        self.initial_estimated_time_s: float = route_data.get("estimated_time_s", 0)
        self.initial_total_distance_m: float = self._total_distance_m

        # Route history (for "before vs after" metrics in Stage 7)
        self.route_history: list[dict] = [{
            "timestamp": time.time(),
            "reason": "initial",
            "total_distance_m": self._total_distance_m,
            "estimated_time_s": self.initial_estimated_time_s,
            "segment_count": len(self.segments),
        }]

    @property
    def is_finished(self) -> bool:
        return self.state in (SimulationState.ARRIVED, SimulationState.CANCELLED)

    def start(self) -> None:
        """Transition from WAITING to RUNNING."""
        if self.state == SimulationState.WAITING:
            self.state = SimulationState.RUNNING
            self.started_at = time.time()   # Stage 7: wall-clock start for elapsed timer
            logger.info("Simulation %s started", self.ambulance_id)

    def cancel(self) -> None:
        """Gracefully stop the simulation."""
        self.state = SimulationState.CANCELLED
        self._cancel_event.set()
        logger.info("Simulation %s cancelled", self.ambulance_id)

    async def wait_for_cancel(self, timeout: float = None):
        """Await cancellation (used by the WebSocket handler for clean shutdown)."""
        try:
            await asyncio.wait_for(self._cancel_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def reroute(self, new_route_data: dict) -> None:
        """
        Splice a new route at the ambulance's current position.

        Design: the ambulance keeps its current (lat, lng) and we find the
        closest node in the new route's node_path.  Everything before that
        node is discarded; the ambulance continues from where it is now
        toward the rest of the new route.

        This avoids:
          1. Teleporting the ambulance backward to the new route's start
          2. Making the ambulance re-traverse already-covered ground
        """
        old_state = self.state
        self.state = SimulationState.REROUTING

        current_lat, current_lng = self.current_position()

        new_coords = [tuple(c) for c in new_route_data["coords"]]
        new_segments_data = new_route_data["segments"]

        # Find closest node in the new route to the ambulance's current position
        best_idx = 0
        best_dist = float("inf")
        for i, (lat, lng) in enumerate(new_coords):
            d = _haversine_m(current_lat, current_lng, lat, lng)
            if d < best_dist:
                best_dist = d
                best_idx = i

        # Slice from the closest node onward
        if best_idx >= len(new_coords) - 1:
            # Already at or past the destination
            self.state = SimulationState.ARRIVED
            return

        trimmed_coords = [(current_lat, current_lng)] + list(new_coords[best_idx:])
        trimmed_segments_data = new_segments_data[max(0, best_idx - 1):]

        # Rebuild segments from the splice point
        if len(trimmed_segments_data) >= len(trimmed_coords):
            trimmed_segments_data = trimmed_segments_data[:len(trimmed_coords) - 1]
        elif len(trimmed_segments_data) < len(trimmed_coords) - 1:
            # Pad with the last segment's data if we're short
            while len(trimmed_segments_data) < len(trimmed_coords) - 1:
                trimmed_segments_data.append(trimmed_segments_data[-1] if trimmed_segments_data else new_segments_data[-1])

        self.coords = trimmed_coords
        self.segments = _build_segments(trimmed_coords, trimmed_segments_data)
        self.signal_nodes_on_route = new_route_data.get("signal_nodes_on_route", [])

        # Reset cursor
        self._seg_idx = 0
        self._seg_progress_m = 0.0
        self._total_distance_m = self._distance_covered_m + sum(s.length_m for s in self.segments)

        self.reroute_count += 1
        self.route_history.append({
            "timestamp": time.time(),
            "reason": "congestion_reroute",
            "splice_node_index": best_idx,
            "splice_distance_m": best_dist,
            "new_remaining_segments": len(self.segments),
            "total_distance_m": self._total_distance_m,
            "estimated_time_s": new_route_data.get("estimated_time_s", 0),
        })

        self.state = SimulationState.RUNNING if old_state == SimulationState.RUNNING else old_state
        logger.info(
            "Simulation %s rerouted (reroute #%d): spliced at node %d (%.0f m away), "
            "%d segments remaining",
            self.ambulance_id, self.reroute_count, best_idx, best_dist, len(self.segments),
        )

    def current_position(self) -> tuple[float, float]:
        """Return the ambulance's current (lat, lng)."""
        if self._seg_idx >= len(self.segments):
            return self.coords[-1]

        seg = self.segments[self._seg_idx]
        fraction = self._seg_progress_m / seg.length_m if seg.length_m > 0 else 1.0
        fraction = min(fraction, 1.0)
        return _interpolate(seg.start_lat, seg.start_lng, seg.end_lat, seg.end_lng, fraction)

    def _current_speed_kmh(self) -> float:
        """
        Compute the ambulance's instantaneous speed on the current segment.

        speed = road_speed × ambulance_factor × demo_multiplier / effective_congestion

        The ambulance factor (1.3x) accounts for siren right-of-way.
        The effective_congestion uses sqrt(raw) to model partial clearing.
        The demo multiplier (default 1.0) lets us speed up the simulation
        for a quicker demo without changing the underlying physics model.
        """
        if self._seg_idx >= len(self.segments):
            return 0.0
        seg = self.segments[self._seg_idx]
        eff_cong = effective_congestion(seg.congestion_factor)
        return (seg.speed_kmh * AMBULANCE_SPEED_FACTOR * self.speed_multiplier) / eff_cong

    def _remaining_distance_m(self) -> float:
        """Distance from current position to destination."""
        if self._seg_idx >= len(self.segments):
            return 0.0
        # Remaining on current segment
        seg = self.segments[self._seg_idx]
        remaining = seg.length_m - self._seg_progress_m
        # Plus all subsequent segments
        for s in self.segments[self._seg_idx + 1:]:
            remaining += s.length_m
        return max(0.0, remaining)

    def _estimate_eta_s(self) -> float:
        """
        Estimate time to arrival based on remaining segments.

        Rather than dividing total remaining distance by current speed
        (which would ignore varying speeds on different road types), we
        sum the expected time for each remaining segment individually.
        This gives a more accurate ETA that accounts for the speed profile
        of the remaining route.
        """
        if self._seg_idx >= len(self.segments):
            return 0.0

        total_s = 0.0

        # Partial current segment
        seg = self.segments[self._seg_idx]
        remaining_on_seg = seg.length_m - self._seg_progress_m
        speed_ms = self._current_speed_kmh() / 3.6
        if speed_ms > 0:
            total_s += remaining_on_seg / speed_ms

        # Full remaining segments
        for s in self.segments[self._seg_idx + 1:]:
            eff_cong = effective_congestion(s.congestion_factor)
            seg_speed_ms = (s.speed_kmh * AMBULANCE_SPEED_FACTOR * self.speed_multiplier) / eff_cong / 3.6
            if seg_speed_ms > 0:
                total_s += s.length_m / seg_speed_ms

        return total_s

    def tick(self, dt: float = 1.0) -> Optional[TelemetryPing]:
        """
        Advance the ambulance by `dt` seconds of real time.

        Returns a TelemetryPing, or None if the simulation is finished.

        The movement algorithm:
        1. Compute distance to travel = speed × dt
        2. Consume that distance along the current segment
        3. If we overshoot the segment end, carry the remainder into the
           next segment (and the next, etc.)
        4. If no segments remain, mark as ARRIVED

        This carry-over logic ensures the ambulance doesn't lose distance
        at segment boundaries — important for accurate ETA.
        """
        if self.is_finished:
            return None

        if self.state != SimulationState.RUNNING:
            return None

        speed_kmh = self._current_speed_kmh()
        speed_ms = speed_kmh / 3.6
        distance_to_travel = speed_ms * dt

        # Advance along segments, carrying over any overshoot
        while distance_to_travel > 0 and self._seg_idx < len(self.segments):
            seg = self.segments[self._seg_idx]
            remaining_on_seg = seg.length_m - self._seg_progress_m

            if distance_to_travel < remaining_on_seg:
                # Stay within current segment
                self._seg_progress_m += distance_to_travel
                self._distance_covered_m += distance_to_travel
                distance_to_travel = 0.0
            else:
                # Complete this segment, move to next
                self._distance_covered_m += remaining_on_seg
                distance_to_travel -= remaining_on_seg
                self._seg_idx += 1
                self._seg_progress_m = 0.0

                # Recalculate speed for new segment
                if self._seg_idx < len(self.segments):
                    speed_kmh = self._current_speed_kmh()
                    speed_ms = speed_kmh / 3.6

        # Check if we've exhausted all segments
        if self._seg_idx >= len(self.segments):
            self.state = SimulationState.ARRIVED
            self.finished_at = time.time()  # Stage 7: wall-clock finish for elapsed time
            lat, lng = self.coords[-1]
            logger.info("Simulation %s ARRIVED at destination", self.ambulance_id)
        else:
            lat, lng = self.current_position()

        # Build the telemetry ping
        remaining_dist = self._remaining_distance_m()
        eta_s = self._estimate_eta_s()

        # Determine current segment info
        if self._seg_idx < len(self.segments):
            cur_seg = self.segments[self._seg_idx]
            heading = _bearing_deg(
                cur_seg.start_lat, cur_seg.start_lng,
                cur_seg.end_lat, cur_seg.end_lng,
            )
            road_name = cur_seg.road_name or "(unnamed road)"
            cong_factor = cur_seg.congestion_factor

            # "Near signal" = within 200m of a signal node (for Stage 4 preview)
            is_near_sig = False
            sig_node = None
            if cur_seg.is_signal_at_dest:
                dist_to_sig = cur_seg.length_m - self._seg_progress_m
                if dist_to_sig < 200:
                    is_near_sig = True
                    sig_node = cur_seg.signal_node_id
        else:
            heading = 0.0
            road_name = "Destination"
            cong_factor = 1.0
            is_near_sig = False
            sig_node = None

        return TelemetryPing(
            ambulance_id=self.ambulance_id,
            lat=lat,
            lng=lng,
            speed_kmh=round(speed_kmh, 1),
            heading_deg=round(heading, 1),
            distance_remaining_m=round(remaining_dist, 1),
            distance_covered_m=round(self._distance_covered_m, 1),
            eta_seconds=round(eta_s, 1),
            current_segment_index=min(self._seg_idx, len(self.segments) - 1),
            total_segments=len(self.segments),
            state=self.state.value,
            timestamp=time.time(),
            current_road_name=road_name,
            congestion_factor=cong_factor,
            is_near_signal=is_near_sig,
            signal_node_id=sig_node,
            reroute_count=self.reroute_count,
        )


# ---------------------------------------------------------------------------
# Global simulation registry
# ---------------------------------------------------------------------------
# Keyed by ambulance_id.  The WebSocket handler registers a simulation here
# so the reroute endpoint can find it.

_simulations: dict[str, AmbulanceSimulation] = {}


def get_simulation(ambulance_id: str) -> Optional[AmbulanceSimulation]:
    return _simulations.get(ambulance_id)


def register_simulation(sim: AmbulanceSimulation) -> None:
    _simulations[sim.ambulance_id] = sim
    logger.info("Simulation registered: %s", sim.ambulance_id)


def unregister_simulation(ambulance_id: str) -> None:
    _simulations.pop(ambulance_id, None)
    logger.info("Simulation unregistered: %s", ambulance_id)


def list_simulations() -> list[dict]:
    """Return summary info for all active simulations."""
    result = []
    for sim in _simulations.values():
        lat, lng = sim.current_position()
        result.append({
            "ambulance_id": sim.ambulance_id,
            "state": sim.state.value,
            "severity": sim.severity,
            "lat": lat,
            "lng": lng,
            "distance_remaining_m": round(sim._remaining_distance_m(), 1),
            "eta_seconds": round(sim._estimate_eta_s(), 1),
            "reroute_count": sim.reroute_count,
            "speed_multiplier": sim.speed_multiplier,
        })
    return result
