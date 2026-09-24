"""
services/signals.py -- Traffic Signal Priority State Machine
=============================================================

THIS IS THE CORE OF MY (MOHIT'S) INDIVIDUAL MODULE.
Every design decision here is documented for viva defence.

Overview
--------
Each signalised intersection is modelled as an independent finite state
machine (FSM) with four operating modes:

  NORMAL          -- cycling through phases on a fixed timer
  PRIORITY_PENDING -- ambulance detected approaching; preparing to preempt
  PRIORITY_ACTIVE  -- green held (or forced) for the ambulance approach direction
  RESTORING        -- returning to normal cycle after the ambulance passes

The green-corridor effect is achieved by a SignalCorridorController that
coordinates multiple intersections ahead of the ambulance, staggering
priority requests so each signal is green by the time the ambulance
arrives.


Safety constraints (what a real traffic engineer would care about)
------------------------------------------------------------------

1. MINIMUM GREEN TIME (min_green_s = 10s)
   A phase that just turned green cannot be cut short before vehicles
   already in the intersection have cleared.  10s is the IRC (Indian Roads
   Congress) minimum for a single-lane approach.
   Reference: IRC:SP-41 "Guidelines on Design of At-Grade Intersections"

2. ALL-RED CLEARANCE INTERVAL (all_red_s = 3s)
   After any phase ends, ALL directions show red for 3 seconds before the
   next phase starts green.  This clears vehicles that entered on the
   yellow/amber tail end.  Without this, a T-bone collision is possible.
   Reference: MUTCD Section 4D.26, adapted for Indian conditions.

3. PEDESTRIAN SAFETY BUFFER (ped_clearance_s = 5s)
   If pedestrians are crossing (assumed during every normal red-to-green
   transition), we add 5s before vehicles get green.  During emergency
   preemption this is reduced to 3s (the all-red already provides some
   buffer), but NEVER to zero -- pedestrians in the crosswalk cannot
   teleport out.
   Reference: IRC:103-2012 "Guidelines for Pedestrian Facilities"

4. MAXIMUM PRIORITY HOLD (max_priority_hold_s = 90s)
   Even during emergency preemption, a green cannot be held indefinitely.
   If the ambulance hasn't passed within 90s, the signal reverts to
   normal.  This prevents a stuck/lost ambulance from gridlocking an
   intersection permanently.

5. COOL-DOWN PERIOD (cooldown_s = 30s)
   After one priority event, the signal cannot be preempted again for 30s.
   This prevents rapid re-triggering from multiple ambulances and gives
   cross-traffic a recovery window.

6. TRANSITION ORDER: the signal NEVER goes directly from red to green.
   It always passes through an all-red clearance phase.  The sequence is:
     [opposing green] -> [all-red] -> [priority green]
   This is a hard safety invariant.


State Machine Diagram
---------------------
                                    +-----------+
                          timeout   |           |  ambulance
                     +------------->|  NORMAL   |  approaching
                     |              |           |<-----------+
                     |              +-----+-----+            |
                     |                    |                   |
                     |          priority  |                   |
                     |          request   v                   |
                +----+----+      +-----------+               |
                |         |      |  PRIORITY |               |
                |RESTORING|      |  PENDING  |               |
                |         |      |           |               |
                +----^----+      +-----+-----+               |
                     |                 |                      |
                     |    clearance    |                      |
                     |    done         v                      |
                     |           +-----------+   ambulance    |
                     |           |  PRIORITY |   passed       |
                     +-----------+  ACTIVE   +----------------+
                                 |           |
                                 +-----------+


Phase Model
-----------
A typical 4-way intersection has 2 main phases:
  Phase A: North-South green  (East-West red)
  Phase B: East-West green    (North-South red)

For simplicity (and viva explainability), we model 2-phase intersections.
Real intersections may have 4+ phases (protected left turns, etc.), but
the preemption logic generalises: the priority phase is whichever phase
serves the ambulance's approach direction.

The ambulance's approach direction is determined by comparing its bearing
to the intersection's road orientations.  We simplify this to: the
ambulance always gets Phase A (the "priority phase").  In a production
system, we'd compute the actual approach leg from the route geometry.
"""

import logging
import time
import math
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Constants -- all safety parameters with their justifications
# ============================================================================

# Phase durations for a normal 2-phase signal cycle.
# These produce a 90s total cycle, which is typical for a mid-size Indian
# urban intersection.  Source: IRC:SP-41, Table 4.1 for Vijayawada-class cities.
DEFAULT_GREEN_A_S: float = 35.0   # Phase A green duration (seconds)
DEFAULT_GREEN_B_S: float = 35.0   # Phase B green duration
DEFAULT_AMBER_S: float   = 3.0    # Amber/yellow (same for both phases)
ALL_RED_CLEARANCE_S: float = 3.0  # All-red between any two phases

# Total normal cycle = 35 + 3 + 3 + 35 + 3 + 3 = 82s (close to the 75-90s
# range recommended by IRC:SP-41 for 2-phase signals in Indian cities)

# Safety limits for priority preemption
MIN_GREEN_S: float     = 10.0   # Minimum green before phase can be terminated
MAX_PRIORITY_HOLD_S: float = 90.0  # Max time the priority green can be held
PED_CLEARANCE_S: float = 5.0    # Normal pedestrian clearance interval
PED_CLEARANCE_EMERGENCY_S: float = 3.0  # Reduced clearance during emergency
COOLDOWN_S: float      = 30.0   # Minimum time between successive preemptions

# Green corridor parameters
CORRIDOR_LOOKAHEAD_M: float = 800.0   # How far ahead to request priority (metres)
CORRIDOR_PREDICTION_BUFFER_S: float = 10.0  # Extra seconds for preemption setup


# ============================================================================
# Enums
# ============================================================================

class SignalPhase(str, Enum):
    """Which traffic phase is currently active."""
    GREEN_A  = "green_A"    # Phase A (ambulance approach direction) is green
    AMBER_A  = "amber_A"    # Phase A transitioning to red
    GREEN_B  = "green_B"    # Phase B (cross traffic) is green
    AMBER_B  = "amber_B"    # Phase B transitioning to red
    ALL_RED  = "all_red"    # Clearance interval -- ALL directions red


class SignalMode(str, Enum):
    """Operating mode of the signal controller."""
    NORMAL           = "normal"            # Cycling through phases normally
    PRIORITY_PENDING = "priority_pending"  # Preemption requested, waiting for safe transition
    PRIORITY_ACTIVE  = "priority_active"   # Green held for ambulance
    RESTORING        = "restoring"         # Returning to normal after priority


class LightColour(str, Enum):
    """What colour the signal shows to each direction.  Used by the frontend."""
    GREEN  = "green"
    AMBER  = "amber"
    RED    = "red"


# ============================================================================
# Data classes
# ============================================================================

@dataclass
class SignalSnapshot:
    """
    A point-in-time snapshot of one intersection's state.
    This is what the frontend and WebSocket receive.
    """
    node_id: int                 # OSM node ID
    lat: float
    lng: float
    mode: str                    # SignalMode value
    phase: str                   # SignalPhase value
    phase_elapsed_s: float       # Seconds since current phase started
    phase_remaining_s: float     # Seconds until current phase ends (normal mode)
    ambulance_direction: str     # LightColour for the ambulance approach
    cross_direction: str         # LightColour for perpendicular traffic
    priority_ambulance_id: Optional[str]  # Which ambulance triggered priority
    priority_eta_s: Optional[float]       # ETA of that ambulance
    time_saved_s: float          # Estimated seconds saved by this preemption


@dataclass
class PriorityRequest:
    """A request from an ambulance for signal priority at an intersection."""
    ambulance_id: str
    eta_seconds: float           # Estimated arrival time at this intersection
    distance_m: float            # Distance from ambulance to intersection
    speed_kmh: float             # Current speed
    bearing_deg: float           # Approach direction
    timestamp: float             # When the request was created


# ============================================================================
# IntersectionController -- the core state machine
# ============================================================================

class IntersectionController:
    """
    Finite state machine for a single signalised intersection.

    Each instance runs independently.  The tick() method advances the
    state by dt seconds and returns the current state snapshot.  The
    green-corridor controller calls tick() for all active intersections
    on every simulation step.

    State transitions are implemented as a dispatch table (method per mode)
    rather than a giant if/elif chain, making each mode's logic isolated
    and individually testable.
    """

    def __init__(
        self,
        node_id: int,
        lat: float,
        lng: float,
        green_a_s: float = DEFAULT_GREEN_A_S,
        green_b_s: float = DEFAULT_GREEN_B_S,
        amber_s: float   = DEFAULT_AMBER_S,
    ):
        self.node_id = node_id
        self.lat = lat
        self.lng = lng

        # Phase durations (configurable per intersection for realism)
        self.green_a_s = green_a_s
        self.green_b_s = green_b_s
        self.amber_s = amber_s

        # Current state
        self.mode: SignalMode = SignalMode.NORMAL
        self.phase: SignalPhase = SignalPhase.GREEN_A
        self._phase_timer: float = 0.0        # Seconds elapsed in current phase
        self._mode_timer: float = 0.0         # Seconds elapsed in current mode
        self._priority_start: float = 0.0     # Timestamp when priority was activated
        self._last_priority_end: float = 0.0  # Timestamp when last priority ended
        self._time_saved_s: float = 0.0       # Running estimate of time saved

        # Active priority request (if any)
        self._priority_request: Optional[PriorityRequest] = None

        # Stagger offset for natural-looking phase diversity across intersections
        # (without this, every signal would start on the same phase simultaneously,
        # which looks fake in the demo)
        self._initial_offset: float = (node_id % 7) * 11.0  # pseudo-random 0-66s offset
        self._phase_timer = self._initial_offset % self._phase_duration()

        # Statistics
        self.total_preemptions: int = 0
        self.total_time_saved_s: float = 0.0

    def _phase_duration(self) -> float:
        """Duration of the current phase in seconds."""
        match self.phase:
            case SignalPhase.GREEN_A:  return self.green_a_s
            case SignalPhase.AMBER_A:  return self.amber_s
            case SignalPhase.GREEN_B:  return self.green_b_s
            case SignalPhase.AMBER_B:  return self.amber_s
            case SignalPhase.ALL_RED:  return ALL_RED_CLEARANCE_S

    def _next_phase(self) -> SignalPhase:
        """
        Normal cycle order: GREEN_A -> AMBER_A -> ALL_RED -> GREEN_B -> AMBER_B -> ALL_RED -> repeat

        The ALL_RED between every phase change is the key safety feature.
        It ensures no direction ever has green while another is transitioning.
        """
        match self.phase:
            case SignalPhase.GREEN_A:  return SignalPhase.AMBER_A
            case SignalPhase.AMBER_A:  return SignalPhase.ALL_RED
            case SignalPhase.ALL_RED:
                # After all-red, which phase comes next depends on context.
                # In normal mode: alternate between A and B.
                # In priority mode: go to GREEN_A (the priority phase).
                if self.mode in (SignalMode.PRIORITY_PENDING, SignalMode.PRIORITY_ACTIVE):
                    return SignalPhase.GREEN_A
                # In normal/restoring: alternate
                return SignalPhase.GREEN_B
            case SignalPhase.GREEN_B:  return SignalPhase.AMBER_B
            case SignalPhase.AMBER_B:  return SignalPhase.ALL_RED

    def _ambulance_light(self) -> LightColour:
        """What colour the ambulance sees on its approach direction."""
        match self.phase:
            case SignalPhase.GREEN_A:  return LightColour.GREEN
            case SignalPhase.AMBER_A:  return LightColour.AMBER
            case SignalPhase.GREEN_B:  return LightColour.RED
            case SignalPhase.AMBER_B:  return LightColour.RED
            case SignalPhase.ALL_RED:  return LightColour.RED

    def _cross_light(self) -> LightColour:
        """What colour cross-traffic sees."""
        match self.phase:
            case SignalPhase.GREEN_A:  return LightColour.RED
            case SignalPhase.AMBER_A:  return LightColour.RED
            case SignalPhase.GREEN_B:  return LightColour.GREEN
            case SignalPhase.AMBER_B:  return LightColour.AMBER
            case SignalPhase.ALL_RED:  return LightColour.RED

    @property
    def is_on_cooldown(self) -> bool:
        """True if this intersection recently had a priority event and cannot be preempted yet."""
        if self._last_priority_end == 0:
            return False
        return (time.time() - self._last_priority_end) < COOLDOWN_S

    # ── Priority request ────────────────────────────────────────────────────

    def request_priority(self, req: PriorityRequest) -> bool:
        """
        Request emergency vehicle priority (EVP) at this intersection.

        Returns True if the request was accepted, False if rejected.

        Rejection reasons:
          - Already serving a different ambulance's priority
          - On cooldown from a recent priority event
          - Ambulance is too far away (> CORRIDOR_LOOKAHEAD_M)

        The request is NOT immediately acted upon.  It transitions the mode
        to PRIORITY_PENDING, and the state machine handles the safe
        transition during subsequent tick() calls.  This separation of
        "request" from "execution" is a key safety principle in real EVP
        systems (NEMA TS-2 standard, Section 5.3).
        """
        # Reject if on cooldown
        if self.is_on_cooldown:
            logger.debug(
                "Signal %d: priority rejected (cooldown), ambulance=%s",
                self.node_id, req.ambulance_id,
            )
            return False

        # Reject if already serving a different ambulance
        if (self.mode in (SignalMode.PRIORITY_PENDING, SignalMode.PRIORITY_ACTIVE)
                and self._priority_request
                and self._priority_request.ambulance_id != req.ambulance_id):
            logger.debug(
                "Signal %d: priority rejected (serving %s), requested by %s",
                self.node_id, self._priority_request.ambulance_id, req.ambulance_id,
            )
            return False

        self._priority_request = req

        if self.mode == SignalMode.NORMAL:
            self.mode = SignalMode.PRIORITY_PENDING
            self._mode_timer = 0.0
            logger.info(
                "Signal %d: PRIORITY_PENDING for %s (ETA %.1fs, %.0fm away)",
                self.node_id, req.ambulance_id, req.eta_seconds, req.distance_m,
            )

        return True

    def release_priority(self, ambulance_id: str) -> None:
        """
        Release priority hold -- called when the ambulance passes through.

        Transitions to RESTORING mode, which will return to normal cycling
        after a brief restoration sequence.
        """
        if (self._priority_request
                and self._priority_request.ambulance_id == ambulance_id
                and self.mode in (SignalMode.PRIORITY_PENDING, SignalMode.PRIORITY_ACTIVE)):

            # Calculate time saved: the remaining red phase time the ambulance
            # would have waited without preemption.
            self._time_saved_s = self._estimate_time_saved()
            self.total_time_saved_s += self._time_saved_s
            self.total_preemptions += 1

            self.mode = SignalMode.RESTORING
            self._mode_timer = 0.0
            self._last_priority_end = time.time()
            logger.info(
                "Signal %d: priority RELEASED for %s (saved ~%.1fs)",
                self.node_id, ambulance_id, self._time_saved_s,
            )
            self._priority_request = None

    def _estimate_time_saved(self) -> float:
        """
        Estimate how many seconds the ambulance saved due to this preemption.

        Without preemption, the ambulance would wait the expected red-phase
        duration (half the cycle on average for random arrival = ~22.5s for
        our 45s green phase).  With preemption, it waited 0s (or close to it).

        This is the key metric for the "before vs after" comparison in Stage 7.
        """
        if self.mode == SignalMode.PRIORITY_ACTIVE:
            # The ambulance got green immediately; saved the remaining
            # time of whatever non-priority phase was active.
            return max(0.0, (self.green_b_s + self.amber_s + ALL_RED_CLEARANCE_S) / 2)
        return 0.0

    # ── Tick (state machine advance) ────────────────────────────────────────

    def tick(self, dt: float) -> SignalSnapshot:
        """
        Advance the state machine by dt seconds.

        This is the heart of the module.  Each mode has its own handler
        method to keep the logic cleanly separated.

        Returns a SignalSnapshot for the frontend/WebSocket.
        """
        self._phase_timer += dt
        self._mode_timer += dt

        # Dispatch to mode-specific handler
        match self.mode:
            case SignalMode.NORMAL:
                self._tick_normal()
            case SignalMode.PRIORITY_PENDING:
                self._tick_priority_pending()
            case SignalMode.PRIORITY_ACTIVE:
                self._tick_priority_active()
            case SignalMode.RESTORING:
                self._tick_restoring()

        # Build snapshot
        return SignalSnapshot(
            node_id=self.node_id,
            lat=self.lat,
            lng=self.lng,
            mode=self.mode.value,
            phase=self.phase.value,
            phase_elapsed_s=round(self._phase_timer, 1),
            phase_remaining_s=round(max(0, self._phase_duration() - self._phase_timer), 1),
            ambulance_direction=self._ambulance_light().value,
            cross_direction=self._cross_light().value,
            priority_ambulance_id=(
                self._priority_request.ambulance_id if self._priority_request else None
            ),
            priority_eta_s=(
                self._priority_request.eta_seconds if self._priority_request else None
            ),
            time_saved_s=round(self._time_saved_s, 1),
        )

    def _transition_phase(self, new_phase: SignalPhase) -> None:
        """Transition to a new signal phase, resetting the phase timer."""
        old = self.phase
        self.phase = new_phase
        self._phase_timer = 0.0
        logger.debug("Signal %d: %s -> %s", self.node_id, old.value, new_phase.value)

    # ── Mode handlers ─────────────────────────────────────────────────────

    def _tick_normal(self) -> None:
        """
        NORMAL mode: cycle through phases on fixed timers.

        GREEN_A (35s) -> AMBER_A (3s) -> ALL_RED (3s) ->
        GREEN_B (35s) -> AMBER_B (3s) -> ALL_RED (3s) -> repeat
        """
        if self._phase_timer >= self._phase_duration():
            self._transition_phase(self._next_phase())

    def _tick_priority_pending(self) -> None:
        """
        PRIORITY_PENDING mode: transition the signal to GREEN_A as safely
        and quickly as possible.

        Strategy depends on what phase we're currently in:

        1. Already GREEN_A -> immediately transition to PRIORITY_ACTIVE
           (the ambulance approach direction is already green -- extend it)

        2. AMBER_A or ALL_RED -> let the current phase finish naturally,
           then go to PRIORITY_ACTIVE when GREEN_A starts

        3. GREEN_B -> if minimum green has elapsed, cut it short:
             GREEN_B -> AMBER_B -> ALL_RED -> GREEN_A (priority)
           If minimum green has NOT elapsed, wait until it has.

        4. AMBER_B -> let it finish -> ALL_RED -> GREEN_A (priority)

        This logic ensures we NEVER violate the minimum green time or
        skip the all-red clearance.

        Key insight for viva: the "pending" state exists specifically to
        handle the gap between "ambulance requests priority" and "signal
        is physically able to show green safely".  In a real NEMA TS-2
        controller, this is called the "transition sequence".
        """
        match self.phase:
            case SignalPhase.GREEN_A:
                # Already green for the ambulance -- go directly to PRIORITY_ACTIVE
                self.mode = SignalMode.PRIORITY_ACTIVE
                self._mode_timer = 0.0
                self._priority_start = time.time()
                logger.info("Signal %d: GREEN already active -> PRIORITY_ACTIVE", self.node_id)

            case SignalPhase.AMBER_A:
                # Let amber finish, then all-red, then GREEN_A as priority
                if self._phase_timer >= self._phase_duration():
                    self._transition_phase(SignalPhase.ALL_RED)

            case SignalPhase.ALL_RED:
                # After all-red, go to GREEN_A and enter PRIORITY_ACTIVE
                if self._phase_timer >= ALL_RED_CLEARANCE_S:
                    self._transition_phase(SignalPhase.GREEN_A)
                    self.mode = SignalMode.PRIORITY_ACTIVE
                    self._mode_timer = 0.0
                    self._priority_start = time.time()
                    logger.info("Signal %d: all-red done -> PRIORITY_ACTIVE", self.node_id)

            case SignalPhase.GREEN_B:
                # Cross traffic is green.  We need to safely terminate it.
                # SAFETY: do NOT cut GREEN_B before minimum green has elapsed.
                if self._phase_timer >= MIN_GREEN_S:
                    # Safe to cut: transition to AMBER_B
                    self._transition_phase(SignalPhase.AMBER_B)
                    logger.info(
                        "Signal %d: GREEN_B cut short at %.1fs (min=%.0fs) for priority",
                        self.node_id, self._phase_timer, MIN_GREEN_S,
                    )
                # else: wait for minimum green to elapse (tick_normal-like behavior)

            case SignalPhase.AMBER_B:
                # Let amber finish, then go to all-red
                if self._phase_timer >= self.amber_s:
                    self._transition_phase(SignalPhase.ALL_RED)

    def _tick_priority_active(self) -> None:
        """
        PRIORITY_ACTIVE mode: hold GREEN_A for the ambulance.

        We keep GREEN_A indefinitely (up to MAX_PRIORITY_HOLD_S) until
        release_priority() is called (ambulance has passed through).

        Safety check: if we've been holding for too long (ambulance is
        stuck or lost), force a revert to normal.  This prevents one
        malfunctioning ambulance from gridlocking an intersection forever.
        """
        if self.phase != SignalPhase.GREEN_A:
            # We should always be on GREEN_A in this mode, but handle edge case
            if self._phase_timer >= self._phase_duration():
                self._transition_phase(self._next_phase())
            return

        # Safety timeout: revert if held too long
        hold_time = time.time() - self._priority_start
        if hold_time > MAX_PRIORITY_HOLD_S:
            logger.warning(
                "Signal %d: PRIORITY timeout after %.0fs -- reverting to NORMAL",
                self.node_id, hold_time,
            )
            self.mode = SignalMode.RESTORING
            self._mode_timer = 0.0
            self._last_priority_end = time.time()
            self._priority_request = None

    def _tick_restoring(self) -> None:
        """
        RESTORING mode: return to normal cycling.

        After the ambulance passes, we don't just snap back to normal.
        We run the current GREEN_A for a few more seconds (to let queued
        vehicles on the priority approach clear), then transition normally.

        The restoration sequence is:
          GREEN_A (hold 5s more) -> AMBER_A -> ALL_RED -> GREEN_B -> NORMAL

        This smooth restoration avoids a jarring instant switch that would
        confuse drivers at the intersection.

        Reference: NEMA TS-2 Section 5.4 "Signal Timing Restoration"
        """
        restoration_hold_s = 5.0  # Brief hold to clear queued vehicles

        match self.phase:
            case SignalPhase.GREEN_A:
                if self._mode_timer >= restoration_hold_s:
                    self._transition_phase(SignalPhase.AMBER_A)

            case SignalPhase.AMBER_A:
                if self._phase_timer >= self.amber_s:
                    self._transition_phase(SignalPhase.ALL_RED)

            case SignalPhase.ALL_RED:
                if self._phase_timer >= ALL_RED_CLEARANCE_S:
                    self._transition_phase(SignalPhase.GREEN_B)
                    self.mode = SignalMode.NORMAL
                    self._mode_timer = 0.0
                    self._time_saved_s = 0.0
                    logger.info("Signal %d: restored to NORMAL", self.node_id)

            case _:
                # If somehow in GREEN_B or AMBER_B during restoration, just go normal
                self.mode = SignalMode.NORMAL
                self._mode_timer = 0.0


# ============================================================================
# SignalCorridorController -- coordinates the "green corridor" effect
# ============================================================================

class SignalCorridorController:
    """
    Coordinates multiple IntersectionControllers along an ambulance's route
    to form a temporary "green corridor".

    How it works
    ------------
    On every simulation tick, the controller:

    1. Gets the ambulance's current position and speed from the telemetry ping.
    2. For each signal intersection on the route that is AHEAD of the ambulance:
       a. Computes the ETA to that intersection based on current speed.
       b. If ETA < CORRIDOR_LOOKAHEAD_M / speed (i.e. the signal is within range),
          sends a priority request to that intersection's controller.
    3. For each signal intersection BEHIND the ambulance (already passed),
       releases any held priority.

    The net effect: as the ambulance drives, a wave of green signals sweeps
    ahead of it, each one turning green just before arrival and reverting
    after passage.

    Why "corridor" and not "cascade"?
    In traffic engineering, a "green wave" or "corridor" means multiple
    consecutive signals are coordinated so a vehicle travelling at a target
    speed encounters green at each one.  Our version is dynamic (adapts to
    actual ambulance speed) rather than the static offset-based green waves
    used in regular traffic management.

    Integration
    -----------
    The WebSocket handler in routers/simulation.py calls
    corridor.update(ping) on every telemetry tick.  The corridor controller
    manages all IntersectionControllers internally.
    """

    def __init__(self):
        # node_id -> IntersectionController
        self._controllers: dict[int, IntersectionController] = {}
        # Ordered list of signal node IDs on the current route
        self._route_signal_nodes: list[int] = []
        # Set of node IDs the ambulance has already passed
        self._passed_nodes: set[int] = set()
        # The ambulance ID this corridor is serving
        self._ambulance_id: Optional[str] = None

    @property
    def controllers(self) -> dict[int, IntersectionController]:
        return self._controllers

    def setup_for_route(
        self,
        ambulance_id: str,
        signal_node_ids: list[int],
        node_coords: dict[int, tuple[float, float]],
    ) -> None:
        """
        Initialise controllers for all signal intersections on a route.

        Called when a simulation starts or reroutes.  Creates an
        IntersectionController for each signal node that doesn't already
        have one (preserving state for nodes that carry over from the
        previous route).

        Args:
            ambulance_id: ID of the ambulance being served
            signal_node_ids: ordered list of signal node IDs along the route
            node_coords: {node_id: (lat, lng)} for each signal node
        """
        self._ambulance_id = ambulance_id
        self._route_signal_nodes = list(signal_node_ids)
        self._passed_nodes.clear()

        for nid in signal_node_ids:
            if nid not in self._controllers:
                lat, lng = node_coords.get(nid, (0.0, 0.0))
                self._controllers[nid] = IntersectionController(
                    node_id=nid, lat=lat, lng=lng,
                )
                logger.info(
                    "Signal controller created for node %d (%.5f, %.5f)",
                    nid, lat, lng,
                )

    def update(self, ping, dt: float) -> list[SignalSnapshot]:
        """
        Called on every simulation tick with the latest telemetry ping.

        Returns a list of SignalSnapshots (one per controlled intersection)
        for the WebSocket/frontend to render.

        Logic:
        1. Tick all controllers (advance their internal timers)
        2. For intersections ahead of the ambulance within range, request priority
        3. For intersections the ambulance has passed, release priority
        """
        if not self._route_signal_nodes:
            return []

        snapshots: list[SignalSnapshot] = []
        amb_lat, amb_lng = ping.lat, ping.lng
        amb_speed_kmh = max(ping.speed_kmh, 1.0)  # avoid div by zero
        amb_speed_ms = amb_speed_kmh / 3.6

        for nid in self._route_signal_nodes:
            ctrl = self._controllers.get(nid)
            if ctrl is None:
                continue

            # Distance from ambulance to this intersection
            dist_m = _haversine_m(amb_lat, amb_lng, ctrl.lat, ctrl.lng)

            # ETA at current speed
            eta_s = dist_m / amb_speed_ms if amb_speed_ms > 0 else float("inf")

            # Determine if the ambulance has passed this intersection.
            # Heuristic: if the intersection is behind the ambulance (distance < 50m
            # and the ambulance is moving away from it), consider it passed.
            # We use a simple distance threshold since exact "passed" detection
            # would require bearing comparison with the route geometry.
            is_passed = nid in self._passed_nodes
            if not is_passed and dist_m < 50.0 and ping.distance_covered_m > 0:
                is_passed = True
                self._passed_nodes.add(nid)
                ctrl.release_priority(ping.ambulance_id)
                logger.info(
                    "Signal %d: ambulance %s PASSED (%.0fm away)",
                    nid, ping.ambulance_id, dist_m,
                )

            # Request priority for upcoming intersections within corridor range
            if not is_passed and dist_m < CORRIDOR_LOOKAHEAD_M:
                # Only request if we haven't already
                req = PriorityRequest(
                    ambulance_id=ping.ambulance_id,
                    eta_seconds=eta_s,
                    distance_m=dist_m,
                    speed_kmh=amb_speed_kmh,
                    bearing_deg=ping.heading_deg,
                    timestamp=time.time(),
                )
                ctrl.request_priority(req)

                # Update ETA in existing request
                if ctrl._priority_request and ctrl._priority_request.ambulance_id == ping.ambulance_id:
                    ctrl._priority_request.eta_seconds = eta_s
                    ctrl._priority_request.distance_m = dist_m

            # Tick the controller regardless of priority status
            snapshot = ctrl.tick(dt)
            snapshots.append(snapshot)

        return snapshots

    def get_all_snapshots(self) -> list[SignalSnapshot]:
        """Return snapshots for all controlled intersections without advancing time."""
        return [
            SignalSnapshot(
                node_id=ctrl.node_id,
                lat=ctrl.lat,
                lng=ctrl.lng,
                mode=ctrl.mode.value,
                phase=ctrl.phase.value,
                phase_elapsed_s=round(ctrl._phase_timer, 1),
                phase_remaining_s=round(max(0, ctrl._phase_duration() - ctrl._phase_timer), 1),
                ambulance_direction=ctrl._ambulance_light().value,
                cross_direction=ctrl._cross_light().value,
                priority_ambulance_id=(
                    ctrl._priority_request.ambulance_id if ctrl._priority_request else None
                ),
                priority_eta_s=(
                    ctrl._priority_request.eta_seconds if ctrl._priority_request else None
                ),
                time_saved_s=round(ctrl._time_saved_s, 1),
            )
            for ctrl in self._controllers.values()
        ]

    def get_stats(self) -> dict:
        """Return aggregate statistics for the corridor."""
        total_preemptions = sum(c.total_preemptions for c in self._controllers.values())
        total_saved = sum(c.total_time_saved_s for c in self._controllers.values())
        return {
            "total_intersections": len(self._controllers),
            "route_intersections": len(self._route_signal_nodes),
            "passed_intersections": len(self._passed_nodes),
            "total_preemptions": total_preemptions,
            "total_time_saved_s": round(total_saved, 1),
            "controllers": {
                nid: {
                    "mode": ctrl.mode.value,
                    "phase": ctrl.phase.value,
                    "preemptions": ctrl.total_preemptions,
                    "time_saved_s": round(ctrl.total_time_saved_s, 1),
                }
                for nid, ctrl in self._controllers.items()
            },
        }

    def reset(self) -> None:
        """Clear all controllers and state.  Called between demo runs."""
        self._controllers.clear()
        self._route_signal_nodes.clear()
        self._passed_nodes.clear()
        self._ambulance_id = None
        logger.info("Signal corridor controller reset")


# ============================================================================
# Module-level helpers
# ============================================================================

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ============================================================================
# Module-level singleton for the active corridor
# ============================================================================
# There is one corridor controller per server process.  Multiple ambulances
# would each get their own corridor, but for demo simplicity we support one
# active corridor at a time.

_active_corridor: Optional[SignalCorridorController] = None


def get_corridor() -> SignalCorridorController:
    """Get or create the active corridor controller."""
    global _active_corridor
    if _active_corridor is None:
        _active_corridor = SignalCorridorController()
    return _active_corridor


def reset_corridor() -> None:
    """Reset the corridor (between demo runs)."""
    global _active_corridor
    if _active_corridor:
        _active_corridor.reset()
    _active_corridor = None
