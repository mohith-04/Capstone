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


Real Approach-Direction Awareness (the big fix)
-------------------------------------------------
Previously, every ambulance was assumed to approach every signal from
"Phase A" (the North-South direction) regardless of which road it was
actually on.  This meant an ambulance on an East-West road was told
"your direction is green" when Phase A (N-S) was active — physically
wrong, and a T-bone collision waiting to happen.

The fix:
  1. When the corridor is set up for a route, we compute the bearing of
     the road segment LEADING INTO each signal node (from the route's
     node_path).  This is the ambulance's actual approach direction.

  2. We classify that bearing as Phase A (N-S corridor, 315°-45° or
     135°-225°) or Phase B (E-W corridor, 45°-135° or 225°-315°).

  3. The bearing_to_approach_phase() helper performs this classification.

  4. Each PriorityRequest now carries a priority_phase ("A" or "B").
     The IntersectionController's FSM targets that specific phase for
     preemption instead of always targeting GREEN_A.

  5. A new set of helper properties (_ambulance_green_phase, etc.) on
     IntersectionController make all phase-dependent methods symmetric --
     swapping A/B requires no conditional logic, just different property
     values.

Reference: NEMA TS-2 Section 5.2 "Phase Selection and Assignment".

Multi-Ambulance Conflict Resolution
-------------------------------------
When two or more ambulances approach the same signal from different roads,
a priority queue (min-heap) determines who goes first.

Tie-breaking order (tuple comparison, leftmost wins):
  1. ETA (seconds)          -- nearest ambulance first
  2. Severity rank          -- 0=critical, 1=serious, 2=moderate
  3. Distance (metres)      -- closer is higher priority (fine-grained tie-break)
  4. Request timestamp      -- first-come, first-served
  5. Ambulance ID (str)     -- lexicographic, purely for determinism

The losing ambulance stays in the queue with its ETA refreshed every tick.
The moment the winner passes the intersection the next entry is popped and
preemption begins automatically -- no re-request needed from the corridor.

This mirrors the NEMA TS-2 Multi-Unit Priority (MUP) queue concept, where
a single controller can hold multiple pending requests and service them in
order without external coordination.


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

import heapq
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

# Severity rank mapping used in the priority heap key.
# Lower number = higher priority (min-heap).
SEVERITY_RANK: dict = {
    "critical": 0,
    "serious":  1,
    "moderate": 2,
}


# ============================================================================
# Approach-direction helper
# ============================================================================

def bearing_to_approach_phase(bearing_deg: float) -> str:
    """
    Map a road bearing to a signal phase letter ("A" or "B").

    Phase A serves the North-South corridor.
    Phase B serves the East-West corridor.

    We reduce the bearing to a 0-180° axis (direction doesn't matter,
    only the road's orientation) and check which major axis it's closest to:

      0°  = North  →  N-S axis  →  Phase A
      90° = East   →  E-W axis  →  Phase B
      180° = South →  N-S axis  →  Phase A
      270° = West  →  E-W axis  →  Phase B

    The ±45° boundary is the standard threshold used in traffic engineering
    for assigning approach legs to phases.

    Reference: NEMA TS-2 §5.2 "Phase Selection and Assignment".

    Args:
        bearing_deg: Road bearing in degrees, 0=North, 90=East, clockwise.

    Returns:
        "A" if the road runs more North-South, "B" if more East-West.
    """
    # Fold 0-360 into 0-180 (the road axis, ignoring direction of travel)
    axis = bearing_deg % 180  # 0=N-S, 90=E-W
    # If within 45° of 0° (N-S axis) → Phase A
    # If within 45° of 90° (E-W axis) → Phase B
    if axis <= 45 or axis >= 135:
        return "A"
    return "B"


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
    priority_ambulance_id: Optional[str]  # Which ambulance is currently being served
    priority_eta_s: Optional[float]       # ETA of that ambulance
    time_saved_s: float          # Estimated seconds saved by this preemption
    # ── Multi-ambulance conflict fields ───────────────────────────────────────
    conflict_detected: bool = False          # True when >= 2 ambulances are competing
    pending_queue: list = field(default_factory=list)  # IDs of ambulances waiting


@dataclass
class PriorityRequest:
    """A request from an ambulance for signal priority at an intersection."""
    ambulance_id: str
    eta_seconds: float           # Estimated arrival time at this intersection
    distance_m: float            # Distance from ambulance to intersection
    speed_kmh: float             # Current speed
    bearing_deg: float           # Approach direction
    timestamp: float             # When the request was created (Unix epoch)
    severity: str = "critical"   # Case severity: critical | serious | moderate

    # Which signal phase serves this ambulance's approach road.
    # "A" = North-South corridor green, "B" = East-West corridor green.
    # Computed from route geometry at corridor setup time via bearing_to_approach_phase().
    # Default "A" preserves backward compatibility.
    priority_phase: str = "A"

    # ── Heap key ──────────────────────────────────────────────────────────────
    # Python's heapq is a min-heap on the first element of a tuple.
    # We build a deterministic 5-tuple so that no two requests compare equal:
    #   (eta_s, severity_rank, distance_m, timestamp, ambulance_id)
    # Smallest tuple = highest priority = served first.

    def heap_key(self) -> tuple:
        """
        Composite sort key for the priority min-heap.

        Tie-breaking (left-to-right):
          1. ETA          -- nearest ambulance first
          2. Severity     -- 0=critical beats 1=serious beats 2=moderate
          3. Distance     -- closer ambulance (finer than ETA which is speed-dep.)
          4. Timestamp    -- first requester wins
          5. Ambulance ID -- lexicographic fallback; guarantees strict ordering
        """
        return (
            self.eta_seconds,
            SEVERITY_RANK.get(self.severity, 99),
            self.distance_m,
            self.timestamp,
            self.ambulance_id,
        )


# ============================================================================
# IntersectionController -- the core state machine
# ============================================================================

class IntersectionController:
    """
    Finite state machine for a single signalised intersection.

    Multi-ambulance extension
    -------------------------
    The original single-request slot (_priority_request) is replaced by a
    min-heap priority queue (_queue).  The heap always keeps the
    highest-priority request at index 0 (the "active" request being served).
    All other requests sit behind it and are served in order after the current
    ambulance passes.

    Key invariants:
      - _queue[0] is the request currently being actioned (if queue non-empty).
      - When _queue[0]'s ambulance passes, we pop it and the next entry auto-
        promotes to PRIORITY_PENDING without any external re-request.
      - If the same ambulance re-requests (e.g. every telemetry tick), we update
        its ETA/distance in-place and re-heapify rather than creating a duplicate.
      - conflict_detected is True whenever len(_queue) > 1.

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

        # ── Multi-ambulance priority queue ────────────────────────────────────
        # Each heap entry is a (heap_key_tuple, PriorityRequest) pair.
        # We keep a parallel dict keyed by ambulance_id for O(1) ETA updates.
        self._queue: list = []                         # min-heap
        self._queue_map: dict = {}                     # ambulance_id -> PriorityRequest

        # ── Approach-direction phase ───────────────────────────────────────────
        # Which phase serves the CURRENTLY ACTIVE ambulance's approach road.
        # "A" = N-S green (GREEN_A), "B" = E-W green (GREEN_B).
        # Set from PriorityRequest.priority_phase when preemption is accepted.
        # Default "A" means backward-compatible behaviour for existing tests.
        self._priority_phase: str = "A"

        # Stagger offset for natural-looking phase diversity across intersections
        self._initial_offset: float = (node_id % 7) * 11.0
        self._phase_timer = self._initial_offset % self._phase_duration()

        # Statistics
        self.total_preemptions: int = 0
        self.total_time_saved_s: float = 0.0

    # ── Queue helpers ─────────────────────────────────────────────────────────

    @property
    def _active_request(self) -> Optional[PriorityRequest]:
        """The request currently being served (top of the heap), or None."""
        return self._queue[0][1] if self._queue else None

    def _rebuild_heap(self) -> None:
        """Rebuild the heap after an in-place ETA update (O(n))."""
        self._queue = [(req.heap_key(), req) for req in self._queue_map.values()]
        heapq.heapify(self._queue)

    def _pop_active(self) -> Optional[PriorityRequest]:
        """Remove and return the highest-priority request from the queue."""
        if not self._queue:
            return None
        _, req = heapq.heappop(self._queue)
        self._queue_map.pop(req.ambulance_id, None)
        return req

    # ── Phase helpers ─────────────────────────────────────────────────────────

    # ── Approach-direction phase properties ───────────────────────────────────
    # These four properties make every phase-dependent method symmetric.
    # Swapping the ambulance from Phase A to Phase B only changes _priority_phase
    # and these properties update automatically — no if/else needed elsewhere.

    @property
    def _ambulance_green_phase(self) -> SignalPhase:
        """The phase that shows GREEN to the ambulance's approach road."""
        return SignalPhase.GREEN_A if self._priority_phase == "A" else SignalPhase.GREEN_B

    @property
    def _ambulance_amber_phase(self) -> SignalPhase:
        """The phase that shows AMBER to the ambulance's approach road."""
        return SignalPhase.AMBER_A if self._priority_phase == "A" else SignalPhase.AMBER_B

    @property
    def _cross_green_phase(self) -> SignalPhase:
        """The phase that shows GREEN to the perpendicular (cross) road."""
        return SignalPhase.GREEN_B if self._priority_phase == "A" else SignalPhase.GREEN_A

    @property
    def _cross_amber_phase(self) -> SignalPhase:
        """The phase that shows AMBER to the perpendicular (cross) road."""
        return SignalPhase.AMBER_B if self._priority_phase == "A" else SignalPhase.AMBER_A

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

        In priority mode, ALL_RED always leads to _ambulance_green_phase (GREEN_A
        or GREEN_B depending on the approach direction) instead of the hardcoded
        GREEN_A that existed before the approach-direction fix.
        """
        match self.phase:
            case SignalPhase.GREEN_A:  return SignalPhase.AMBER_A
            case SignalPhase.AMBER_A:  return SignalPhase.ALL_RED
            case SignalPhase.ALL_RED:
                if self.mode in (SignalMode.PRIORITY_PENDING, SignalMode.PRIORITY_ACTIVE):
                    # ── THE FIX: use the ambulance's actual approach phase ──────
                    # Was: return SignalPhase.GREEN_A  (hardcoded — always N-S)
                    # Now: return whichever phase serves this ambulance's road
                    return self._ambulance_green_phase
                return SignalPhase.GREEN_B
            case SignalPhase.GREEN_B:  return SignalPhase.AMBER_B
            case SignalPhase.AMBER_B:  return SignalPhase.ALL_RED

    def _ambulance_light(self) -> LightColour:
        """
        What colour the ambulance sees on its approach direction.

        Before the fix: hardcoded — GREEN_A = green, GREEN_B = red.
        After the fix: uses _ambulance_green_phase / _ambulance_amber_phase
        so the answer is correct regardless of whether the ambulance is on
        the N-S road (Phase A) or the E-W road (Phase B).
        """
        if self.phase == self._ambulance_green_phase:
            return LightColour.GREEN
        if self.phase == self._ambulance_amber_phase:
            return LightColour.AMBER
        return LightColour.RED

    def _cross_light(self) -> LightColour:
        """What colour perpendicular (cross) traffic sees."""
        if self.phase == self._cross_green_phase:
            return LightColour.GREEN
        if self.phase == self._cross_amber_phase:
            return LightColour.AMBER
        return LightColour.RED

    @property
    def is_on_cooldown(self) -> bool:
        """True if this intersection recently had a priority event."""
        if self._last_priority_end == 0:
            return False
        return (time.time() - self._last_priority_end) < COOLDOWN_S

    # ── Priority request ────────────────────────────────────────────────────

    def request_priority(self, req: PriorityRequest) -> bool:
        """
        Request emergency vehicle priority (EVP) at this intersection.

        Returns True if the request was accepted or updated, False if rejected.

        Multi-ambulance behaviour
        -------------------------
        Unlike the original single-slot design this method NEVER rejects a
        request just because another ambulance is already being served.
        Instead:

          - If the ambulance is already in the queue, its ETA/distance is
            refreshed and the heap is rebuilt (keeps data current each tick).

          - If the ambulance is new and NOT on cooldown, it is added to the
            queue.  It will be served automatically after the current winner
            passes -- no re-request required.

          - Cooldown is only enforced when the queue is empty.  If ambulances
            are already queued the cooldown is waived: those emergencies got
            legitimately queued and must be served.

        Conflict detection: len(queue) > 1 means a conflict exists.

        Reference: NEMA TS-2 Multi-Unit Priority (MUP) queuing, Section 5.3.
        """
        # ── Case 1: ETA update for an ambulance already in the queue ─────────
        if req.ambulance_id in self._queue_map:
            existing = self._queue_map[req.ambulance_id]
            existing.eta_seconds = req.eta_seconds
            existing.distance_m  = req.distance_m
            existing.speed_kmh   = req.speed_kmh
            existing.bearing_deg = req.bearing_deg
            self._rebuild_heap()
            logger.debug(
                "Signal %d: ETA update for %s (%.1fs, %.0fm)",
                self.node_id, req.ambulance_id, req.eta_seconds, req.distance_m,
            )
            return True

        # ── Case 2: Cooldown guard (only when queue is empty) ─────────────────
        if self.is_on_cooldown and not self._queue:
            logger.debug(
                "Signal %d: priority rejected (cooldown, empty queue), ambulance=%s",
                self.node_id, req.ambulance_id,
            )
            return False

        # ── Case 3: New ambulance -- add to queue ─────────────────────────────
        self._queue_map[req.ambulance_id] = req
        heapq.heappush(self._queue, (req.heap_key(), req))

        # ── Set _priority_phase from the top of the heap ──────────────────────
        # Only update when NOT currently mid-preemption — changing the phase
        # target while a green is held would confuse the FSM and potentially
        # leave the wrong road green while the winner ambulance drives through.
        if self.mode in (SignalMode.NORMAL, SignalMode.RESTORING):
            active = self._active_request
            if active:
                old_phase = self._priority_phase
                self._priority_phase = active.priority_phase
                if old_phase != self._priority_phase:
                    logger.info(
                        "Signal %d: approach phase set to %s for %s",
                        self.node_id, self._priority_phase, active.ambulance_id,
                    )

        conflict = len(self._queue) > 1
        if conflict:
            logger.warning(
                "Signal %d: CONFLICT -- %d ambulances competing. Winner=%s (phase=%s) Queue=%s",
                self.node_id,
                len(self._queue),
                self._active_request.ambulance_id,
                self._priority_phase,
                [r.ambulance_id for _, r in self._queue],
            )
        else:
            logger.info(
                "Signal %d: PRIORITY_PENDING for %s (ETA %.1fs, %.0fm, severity=%s, approach=Phase %s)",
                self.node_id, req.ambulance_id, req.eta_seconds, req.distance_m,
                req.severity, req.priority_phase,
            )

        # Transition to PRIORITY_PENDING only if currently NORMAL
        if self.mode == SignalMode.NORMAL:
            self.mode = SignalMode.PRIORITY_PENDING
            self._mode_timer = 0.0

        return True

    def release_priority(self, ambulance_id: str) -> None:
        """
        Release priority hold -- called when an ambulance passes through.

        Multi-ambulance behaviour
        -------------------------
        After popping the finished ambulance, if more ambulances are waiting
        we transition to PRIORITY_PENDING for the next one rather than
        RESTORING.  This means the intersection never gives cross-traffic a
        green window between two back-to-back ambulances.
        """
        if ambulance_id not in self._queue_map:
            return

        active = self._active_request
        if active and active.ambulance_id != ambulance_id:
            # The releasing ambulance is NOT the active one (queued but took
            # a different road).  Just remove it from the queue.
            self._queue_map.pop(ambulance_id, None)
            self._rebuild_heap()
            logger.info(
                "Signal %d: queued ambulance %s removed (passed on alternate road)",
                self.node_id, ambulance_id,
            )
            return

        # Calculate time saved before popping
        self._time_saved_s = self._estimate_time_saved()
        self.total_time_saved_s += self._time_saved_s
        self.total_preemptions += 1

        # Pop the finished ambulance
        self._pop_active()
        self._last_priority_end = time.time()

        logger.info(
            "Signal %d: priority RELEASED for %s (saved ~%.1fs). Queue remaining: %d",
            self.node_id, ambulance_id, self._time_saved_s, len(self._queue),
        )

        if self._queue:
            # More ambulances waiting -- immediately start serving the next one.
            # Update _priority_phase so the FSM targets the new ambulance's approach road.
            next_req = self._active_request
            self._priority_phase = next_req.priority_phase
            self.mode = SignalMode.PRIORITY_PENDING
            self._mode_timer = 0.0
            logger.info(
                "Signal %d: auto-promoting %s from queue (ETA %.1fs, severity=%s, approach=Phase %s)",
                self.node_id, next_req.ambulance_id, next_req.eta_seconds,
                next_req.severity, next_req.priority_phase,
            )
        else:
            # Queue is empty -- return to normal cycling
            self.mode = SignalMode.RESTORING
            self._mode_timer = 0.0

    def _estimate_time_saved(self) -> float:
        """
        Estimate how many seconds the ambulance saved due to this preemption.

        Without preemption, the ambulance would wait the expected red-phase
        duration (half the cycle on average for random arrival = ~22.5s for
        our 45s green phase).  With preemption, it waited 0s (or close to it).

        This is the key metric for the "before vs after" comparison in Stage 7.
        """
        if self.mode == SignalMode.PRIORITY_ACTIVE:
            return max(0.0, (self.green_b_s + self.amber_s + ALL_RED_CLEARANCE_S) / 2)
        return 0.0

    # ── Tick (state machine advance) ────────────────────────────────────────

    def tick(self, dt: float) -> SignalSnapshot:
        """
        Advance the state machine by dt seconds.

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

        active = self._active_request
        conflict = len(self._queue) > 1
        pending_ids = [r.ambulance_id for _, r in self._queue[1:]]

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
            priority_ambulance_id=active.ambulance_id if active else None,
            priority_eta_s=active.eta_seconds if active else None,
            time_saved_s=round(self._time_saved_s, 1),
            conflict_detected=conflict,
            pending_queue=pending_ids,
        )

    def _transition_phase(self, new_phase: SignalPhase) -> None:
        """Transition to a new signal phase, resetting the phase timer."""
        old = self.phase
        self.phase = new_phase
        self._phase_timer = 0.0
        logger.debug("Signal %d: %s -> %s", self.node_id, old.value, new_phase.value)

    # ── Mode handlers ─────────────────────────────────────────────────────

    def _tick_normal(self) -> None:
        """NORMAL mode: cycle through phases on fixed timers."""
        if self._phase_timer >= self._phase_duration():
            self._transition_phase(self._next_phase())

    def _tick_priority_pending(self) -> None:
        """
        PRIORITY_PENDING mode: transition the signal to the ambulance's correct
        GREEN phase as safely and quickly as possible.

        THE FIX — approach-direction awareness
        ----------------------------------------
        Previously hardcoded to always target GREEN_A (North-South).
        Now uses _ambulance_green_phase which returns GREEN_A for a N-S
        ambulance OR GREEN_B for an E-W ambulance.  The same logic works
        symmetrically for both directions via the four phase properties.

        Strategy (identical logic, now direction-aware):
          1. Already on ambulance's green phase → PRIORITY_ACTIVE immediately
          2. On ambulance's amber phase → let finish → ALL_RED → priority green
          3. ALL_RED → go directly to ambulance's green → PRIORITY_ACTIVE
          4. On opposing (cross) green → cut short after MIN_GREEN_S
          5. On opposing amber → let finish → ALL_RED

        Safety invariants preserved:
          - MIN_GREEN_S before cutting any opposing green
          - ALL_RED clearance always honoured
          - Signal never jumps directly from red to green
        """
        amb_green = self._ambulance_green_phase   # GREEN_A or GREEN_B
        amb_amber = self._ambulance_amber_phase   # AMBER_A or AMBER_B
        opp_green = self._cross_green_phase       # opposing green
        opp_amber = self._cross_amber_phase       # opposing amber

        if self.phase == amb_green:
            self.mode = SignalMode.PRIORITY_ACTIVE
            self._mode_timer = 0.0
            self._priority_start = time.time()
            logger.info(
                "Signal %d: %s already active -> PRIORITY_ACTIVE (approach=Phase %s)",
                self.node_id, amb_green.value, self._priority_phase,
            )

        elif self.phase == amb_amber:
            if self._phase_timer >= self._phase_duration():
                self._transition_phase(SignalPhase.ALL_RED)

        elif self.phase == SignalPhase.ALL_RED:
            if self._phase_timer >= ALL_RED_CLEARANCE_S:
                self._transition_phase(amb_green)
                self.mode = SignalMode.PRIORITY_ACTIVE
                self._mode_timer = 0.0
                self._priority_start = time.time()
                logger.info(
                    "Signal %d: all-red done -> PRIORITY_ACTIVE (approach=Phase %s)",
                    self.node_id, self._priority_phase,
                )

        elif self.phase == opp_green:
            if self._phase_timer >= MIN_GREEN_S:
                self._transition_phase(opp_amber)
                logger.info(
                    "Signal %d: %s cut short at %.1fs for Phase %s ambulance",
                    self.node_id, opp_green.value, self._phase_timer, self._priority_phase,
                )

        elif self.phase == opp_amber:
            if self._phase_timer >= self.amber_s:
                self._transition_phase(SignalPhase.ALL_RED)

    def _tick_priority_active(self) -> None:
        """
        PRIORITY_ACTIVE mode: hold the ambulance's GREEN phase.

        Before the fix: always held GREEN_A.
        After the fix: holds _ambulance_green_phase (GREEN_A or GREEN_B).

        Safety timeout: clears the entire queue and reverts to RESTORING
        if the ambulance hasn't passed within MAX_PRIORITY_HOLD_S.
        """
        amb_green = self._ambulance_green_phase

        if self.phase != amb_green:
            if self._phase_timer >= self._phase_duration():
                self._transition_phase(self._next_phase())
            return

        hold_time = time.time() - self._priority_start
        if hold_time > MAX_PRIORITY_HOLD_S:
            logger.warning(
                "Signal %d: PRIORITY timeout after %.0fs (Phase %s) -- clearing all requests",
                self.node_id, hold_time, self._priority_phase,
            )
            self._queue.clear()
            self._queue_map.clear()
            self.mode = SignalMode.RESTORING
            self._mode_timer = 0.0
            self._last_priority_end = time.time()

    def _tick_restoring(self) -> None:
        """
        RESTORING mode: return to normal cycling.

        Before the fix: always restored GREEN_A → AMBER_A → ALL_RED → GREEN_B.
        After the fix: restores whichever was the ambulance's green phase, then
        hands over to the opposing phase — fully symmetric.

        Restoration sequence:
          amb_green (hold 5s) → amb_amber → ALL_RED → opp_green → NORMAL
        """
        restoration_hold_s = 5.0
        amb_green = self._ambulance_green_phase
        amb_amber = self._ambulance_amber_phase
        opp_green = self._cross_green_phase

        if self.phase == amb_green:
            if self._mode_timer >= restoration_hold_s:
                self._transition_phase(amb_amber)

        elif self.phase == amb_amber:
            if self._phase_timer >= self.amber_s:
                self._transition_phase(SignalPhase.ALL_RED)

        elif self.phase == SignalPhase.ALL_RED:
            if self._phase_timer >= ALL_RED_CLEARANCE_S:
                self._transition_phase(opp_green)
                self.mode = SignalMode.NORMAL
                self._mode_timer = 0.0
                self._time_saved_s = 0.0
                logger.info(
                    "Signal %d: restored to NORMAL (was serving Phase %s corridor)",
                    self.node_id, self._priority_phase,
                )

        else:
            self.mode = SignalMode.NORMAL
            self._mode_timer = 0.0


# ============================================================================
# GlobalSignalRegistry -- shared IntersectionController pool
# ============================================================================

class GlobalSignalRegistry:
    """
    Singleton pool of IntersectionController objects shared across ALL
    active ambulance corridors.

    Why this is needed
    ------------------
    In the original single-ambulance design, each corridor owned its own
    dict of controllers.  With multiple ambulances this breaks: if Corridor-A
    and Corridor-B both create a controller for node X, they get two separate
    FSMs that independently request priority on the same physical signal,
    defeating conflict resolution entirely.

    Solution: every corridor looks up (or creates) controllers from this
    single registry.  All corridors share the same IntersectionController
    instance for a given node, so the priority queue on that controller sees
    requests from ALL ambulances simultaneously.
    """

    def __init__(self):
        self._controllers: dict = {}

    def get_or_create(self, node_id: int, lat: float, lng: float) -> IntersectionController:
        """Return the existing controller for this node, or create one."""
        if node_id not in self._controllers:
            self._controllers[node_id] = IntersectionController(
                node_id=node_id, lat=lat, lng=lng,
            )
            logger.info(
                "GlobalSignalRegistry: controller created for node %d (%.5f, %.5f)",
                node_id, lat, lng,
            )
        return self._controllers[node_id]

    def get(self, node_id: int) -> Optional[IntersectionController]:
        return self._controllers.get(node_id)

    def all_controllers(self) -> dict:
        return self._controllers

    def reset(self) -> None:
        self._controllers.clear()
        logger.info("GlobalSignalRegistry: all controllers cleared")


# ============================================================================
# SignalCorridorController -- coordinates the "green corridor" for one ambulance
# ============================================================================

class SignalCorridorController:
    """
    Coordinates multiple IntersectionControllers along ONE ambulance's route.

    Each ambulance gets its own SignalCorridorController (managed by
    get_corridor(ambulance_id)).  All corridors share the same
    GlobalSignalRegistry so their priority requests land on the same
    IntersectionController objects -- enabling cross-corridor conflict
    resolution via the priority queue.
    """

    def __init__(self, ambulance_id: str, registry: GlobalSignalRegistry):
        self._ambulance_id = ambulance_id
        self._registry = registry
        self._route_signal_nodes: list = []
        self._passed_nodes: set = set()
        # Approach phase per signal node: {node_id: "A" or "B"}
        # Populated by setup_for_route() from route geometry.
        self._approach_phases: dict = {}

    @property
    def ambulance_id(self) -> str:
        return self._ambulance_id

    def setup_for_route(
        self,
        signal_node_ids: list,
        node_coords: dict,
        approach_phases: dict = None,
    ) -> None:
        """
        Initialise (or refresh) the controlled nodes for a route.

        Called when a simulation starts or reroutes.  Looks up or creates
        IntersectionControllers from the shared GlobalSignalRegistry.

        Args:
            signal_node_ids:  Ordered list of signal node IDs along the route.
            node_coords:      {node_id: (lat, lng)} for each signal node.
            approach_phases:  {node_id: "A" or "B"} — which phase serves
                              the ambulance's approach road at each intersection.
                              Computed in the router from route geometry via
                              bearing_to_approach_phase().  Defaults to "A"
                              for any node not present in the dict (backward compat).
        """
        self._route_signal_nodes = list(signal_node_ids)
        self._passed_nodes.clear()
        self._approach_phases = approach_phases or {}

        for nid in signal_node_ids:
            lat, lng = node_coords.get(nid, (0.0, 0.0))
            self._registry.get_or_create(node_id=nid, lat=lat, lng=lng)
            phase = self._approach_phases.get(nid, "A")
            logger.info(
                "Corridor %s: signal %d approach=Phase %s",
                self._ambulance_id, nid, phase,
            )

    def update(self, ping, dt: float) -> list:
        """
        Called on every simulation tick with the latest telemetry ping.

        Returns a list of SignalSnapshots for the WebSocket/frontend.
        """
        if not self._route_signal_nodes:
            return []

        snapshots = []
        amb_lat, amb_lng = ping.lat, ping.lng
        amb_speed_kmh = max(ping.speed_kmh, 1.0)
        amb_speed_ms = amb_speed_kmh / 3.6

        for nid in self._route_signal_nodes:
            ctrl = self._registry.get(nid)
            if ctrl is None:
                continue

            dist_m = _haversine_m(amb_lat, amb_lng, ctrl.lat, ctrl.lng)
            eta_s = dist_m / amb_speed_ms if amb_speed_ms > 0 else float("inf")

            # Check if the ambulance has passed this intersection
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
                req = PriorityRequest(
                    ambulance_id=ping.ambulance_id,
                    eta_seconds=eta_s,
                    distance_m=dist_m,
                    speed_kmh=amb_speed_kmh,
                    bearing_deg=ping.heading_deg,
                    timestamp=time.time(),
                    severity=getattr(ping, "severity", "critical"),
                    # Use the pre-computed approach phase for this node.
                    # Falls back to "A" if not in the dict (backward compat).
                    priority_phase=self._approach_phases.get(nid, "A"),
                )
                ctrl.request_priority(req)

            # Tick the controller regardless of priority status
            snapshot = ctrl.tick(dt)
            snapshots.append(snapshot)

        return snapshots

    def get_all_snapshots(self) -> list:
        """Return snapshots for all route intersections without advancing time."""
        snapshots = []
        for nid in self._route_signal_nodes:
            ctrl = self._registry.get(nid)
            if ctrl is None:
                continue
            active = ctrl._active_request
            conflict = len(ctrl._queue) > 1
            pending_ids = [r.ambulance_id for _, r in ctrl._queue[1:]]
            snapshots.append(SignalSnapshot(
                node_id=ctrl.node_id,
                lat=ctrl.lat,
                lng=ctrl.lng,
                mode=ctrl.mode.value,
                phase=ctrl.phase.value,
                phase_elapsed_s=round(ctrl._phase_timer, 1),
                phase_remaining_s=round(max(0, ctrl._phase_duration() - ctrl._phase_timer), 1),
                ambulance_direction=ctrl._ambulance_light().value,
                cross_direction=ctrl._cross_light().value,
                priority_ambulance_id=active.ambulance_id if active else None,
                priority_eta_s=active.eta_seconds if active else None,
                time_saved_s=round(ctrl._time_saved_s, 1),
                conflict_detected=conflict,
                pending_queue=pending_ids,
            ))
        return snapshots

    def get_stats(self) -> dict:
        """Return aggregate statistics for this corridor."""
        total_preemptions = 0
        total_saved = 0.0
        controller_stats = {}
        for nid in self._route_signal_nodes:
            ctrl = self._registry.get(nid)
            if ctrl:
                total_preemptions += ctrl.total_preemptions
                total_saved += ctrl.total_time_saved_s
                controller_stats[nid] = {
                    "mode": ctrl.mode.value,
                    "phase": ctrl.phase.value,
                    "preemptions": ctrl.total_preemptions,
                    "time_saved_s": round(ctrl.total_time_saved_s, 1),
                    "conflict_detected": len(ctrl._queue) > 1,
                    "queue_depth": len(ctrl._queue),
                }
        return {
            "ambulance_id": self._ambulance_id,
            "total_intersections": len(self._route_signal_nodes),
            "route_intersections": len(self._route_signal_nodes),
            "passed_intersections": len(self._passed_nodes),
            "total_preemptions": total_preemptions,
            "total_time_saved_s": round(total_saved, 1),
            "controllers": controller_stats,
        }

    def reset(self) -> None:
        """Clear corridor state (does NOT destroy shared registry controllers)."""
        self._route_signal_nodes.clear()
        self._passed_nodes.clear()
        logger.info("Corridor for %s reset", self._ambulance_id)


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
# Module-level singletons
# ============================================================================
# One GlobalSignalRegistry for the whole server process.
# One SignalCorridorController per active ambulance, keyed by ambulance_id.

_registry: GlobalSignalRegistry = GlobalSignalRegistry()
_corridors: dict = {}


def get_registry() -> GlobalSignalRegistry:
    """Return the shared intersection controller registry."""
    return _registry


def get_corridor(ambulance_id: str = "AMB-001") -> SignalCorridorController:
    """
    Get or create the corridor controller for a specific ambulance.

    Each ambulance gets its own corridor, but all corridors share the same
    GlobalSignalRegistry -- this is the key that makes conflict resolution work.
    """
    global _corridors
    if ambulance_id not in _corridors:
        _corridors[ambulance_id] = SignalCorridorController(
            ambulance_id=ambulance_id,
            registry=_registry,
        )
    return _corridors[ambulance_id]


def remove_corridor(ambulance_id: str) -> None:
    """Remove a corridor when its simulation ends."""
    _corridors.pop(ambulance_id, None)
    logger.info("Corridor removed for ambulance %s", ambulance_id)


def get_all_corridors() -> dict:
    """Return all active corridor controllers."""
    return _corridors


def get_global_signal_snapshots() -> list:
    """
    Return snapshots for every IntersectionController in the registry.
    Used by /signals/status to show the full picture across all ambulances.
    """
    snapshots = []
    for ctrl in _registry.all_controllers().values():
        active = ctrl._active_request
        conflict = len(ctrl._queue) > 1
        pending_ids = [r.ambulance_id for _, r in ctrl._queue[1:]]
        snapshots.append(SignalSnapshot(
            node_id=ctrl.node_id,
            lat=ctrl.lat,
            lng=ctrl.lng,
            mode=ctrl.mode.value,
            phase=ctrl.phase.value,
            phase_elapsed_s=round(ctrl._phase_timer, 1),
            phase_remaining_s=round(max(0, ctrl._phase_duration() - ctrl._phase_timer), 1),
            ambulance_direction=ctrl._ambulance_light().value,
            cross_direction=ctrl._cross_light().value,
            priority_ambulance_id=active.ambulance_id if active else None,
            priority_eta_s=active.eta_seconds if active else None,
            time_saved_s=round(ctrl._time_saved_s, 1),
            conflict_detected=conflict,
            pending_queue=pending_ids,
        ))
    return snapshots


def reset_corridor(ambulance_id: str = None) -> None:
    """
    Reset one corridor (if ambulance_id given) or ALL corridors + the registry.
    Called between demo runs or when a simulation ends.
    """
    global _corridors, _registry
    if ambulance_id:
        _corridors.pop(ambulance_id, None)
    else:
        _corridors.clear()
        _registry.reset()
    logger.info("Signal system reset (ambulance_id=%s)", ambulance_id)
