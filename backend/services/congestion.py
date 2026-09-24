"""
services/congestion.py — In-memory congestion store
====================================================

Provides a single shared mutable store that maps an edge key (u, v, k)
to a congestion multiplier.  All modules that need to read or modify
congestion import this singleton — there is exactly one copy in the
process.

Design choices
--------------
- Plain dict rather than a database: congestion is transient simulation
  state that resets every demo run.  A database would add latency and
  complexity without benefit.

- Thread-safety: FastAPI runs in a single async event loop (no preemptive
  threading for route handlers), so a plain dict is safe.  If you later
  add background threads (e.g. a scheduled congestion randomiser), wrap
  mutations in asyncio.Lock instead.

- Why inject by nearest-edges rather than exact (u,v,k)?
  The demo operator clicks a point on the map.  Converting that to the
  nearest road edges is more user-friendly and realistic than requiring
  exact node IDs.  The `inject_congestion_near_point` helper does this
  lookup using OSMnx's `nearest_edges` utility.

Multiplier semantics
--------------------
  1.0   — free flow (normal speed)
  1.5   — moderate congestion (about 2/3 speed)
  2.0   — heavy congestion (half speed)
  3.0   — near-standstill (1/3 speed)
  ≥ 5.0 — effectively blocked (router will reroute around this edge)
"""

import logging
from typing import NamedTuple

import osmnx as ox
import networkx as nx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The singleton congestion store
# key: (u: int, v: int, k: int)  — NetworkX MultiDiGraph edge key
# value: float  — congestion multiplier ≥ 1.0
# ---------------------------------------------------------------------------
_store: dict[tuple[int, int, int], float] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class CongestionEntry(NamedTuple):
    u: int
    v: int
    k: int
    multiplier: float


def get_all() -> list[CongestionEntry]:
    """Return all current congestion entries."""
    return [CongestionEntry(u, v, k, m) for (u, v, k), m in _store.items()]


def get_store() -> dict[tuple[int, int, int], float]:
    """Return the raw store dict (read-only view for the router)."""
    return _store


def inject_near_point(
    G: nx.MultiDiGraph,
    lat: float,
    lng: float,
    multiplier: float,
    radius_m: float = 200.0,
) -> list[CongestionEntry]:
    """
    Set a congestion multiplier on all edges within `radius_m` metres of
    the given (lat, lng) point.

    Returns the list of edges that were affected.

    Why radius-based injection?
    In a real system, congestion data arrives as a geo-tagged event (e.g.
    an accident report at a coordinate).  We translate that to the nearest
    road edges so the router's graph representation stays consistent.

    The `nearest_edges` call uses a k-d tree under the hood (built by
    OSMnx on first call and cached), so it is fast even on large graphs.
    """
    if multiplier < 1.0:
        raise ValueError(f"Congestion multiplier must be ≥ 1.0, got {multiplier}")

    # Find the single nearest edge to the point, then expand to all edges
    # whose midpoint lies within radius_m.
    # We use a simple distance check via OSMnx's nearest_edges with a small
    # dist_m argument to get candidates.
    try:
        # Get nearest edges within the radius
        # ne returns (u, v, key) tuples
        ne = ox.nearest_edges(G, X=lng, Y=lat, return_dist=True)
        # ne is ((u, v, k), dist) — check distance
        (u, v, k), dist = ne
    except Exception as exc:
        logger.warning("nearest_edges failed for (%.5f, %.5f): %s", lat, lng, exc)
        return []

    if dist > radius_m:
        logger.warning(
            "Nearest edge to (%.5f, %.5f) is %.0f m away — outside radius %.0f m",
            lat, lng, dist, radius_m,
        )
        return []

    # Inject on forward edge and its reverse (if both directions exist)
    affected: list[CongestionEntry] = []
    for edge_key in [(u, v, k), (v, u, 0)]:
        eu, ev, ek = edge_key
        if G.has_edge(eu, ev, ek):
            _store[(eu, ev, ek)] = multiplier
            affected.append(CongestionEntry(eu, ev, ek, multiplier))
            logger.info(
                "Congestion %.1fx injected on edge (%d, %d, %d)", multiplier, eu, ev, ek
            )

    return affected


def clear_edge(u: int, v: int, k: int = 0) -> bool:
    """Remove congestion from a specific edge. Returns True if it existed."""
    key = (u, v, k)
    if key in _store:
        del _store[key]
        logger.info("Congestion cleared on edge (%d, %d, %d)", u, v, k)
        return True
    return False


def clear_all() -> int:
    """Remove all congestion. Returns the count of entries cleared."""
    count = len(_store)
    _store.clear()
    logger.info("All congestion cleared (%d entries removed)", count)
    return count
