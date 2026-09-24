"""
services/router.py — A* routing engine
=======================================

Computes the optimal route between two geographic coordinates using
NetworkX's A* algorithm with a custom Haversine heuristic and the
composite travel-time edge weights defined in graph.py.

Why A* instead of plain Dijkstra?
----------------------------------
Dijkstra's algorithm expands nodes in order of cumulative cost, exploring
in all directions uniformly.  On a city-scale graph with ~10,000 nodes this
is fine, but A* improves on it by adding a *heuristic function* h(n, goal)
that estimates the remaining cost from any node to the goal.

For geographic routing the heuristic is the Haversine great-circle distance
divided by the maximum possible speed on the network.  This is *admissible*
(never overestimates the true remaining cost) because:

  true_remaining_time ≥ straight_line_distance / max_speed

Admissibility guarantees that A* still finds the optimal path (same
guarantee as Dijkstra) while typically expanding 30–60% fewer nodes on
city graphs — important when the simulation calls re-route every few seconds.

Reference: Hart, Nilsson & Raphael (1968) "A formal basis for the heuristic
determination of minimum cost paths." IEEE Trans. Systems Science.

Algorithm complexity
--------------------
A* with an admissible heuristic: O((V + E) log V) in the worst case
(same as Dijkstra), but with constant-factor improvement from node pruning.
"""

import logging
import math
from dataclasses import dataclass, field
from functools import partial

import networkx as nx
import osmnx as ox

from .graph import compute_edge_weight, get_hazard_edges, get_signal_nodes
from .congestion import get_store

logger = logging.getLogger(__name__)

# Maximum speed used as the A* heuristic denominator.
# Must be ≥ the fastest road in the graph to preserve admissibility.
# 80 km/h (22.22 m/s) matches our motorway default.
_MAX_SPEED_MS: float = 80.0 / 3.6   # 22.22 m/s


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RouteSegment:
    """One road segment (edge) in the computed route."""
    u: int
    v: int
    k: int
    length_m: float
    name: str            # OSM road name, or empty string
    highway: str         # OSM highway type (e.g. "primary")
    speed_kph: float
    base_time_s: float
    congestion_factor: float
    is_signal_at_dest: bool
    is_hazard: bool
    composite_weight_s: float


@dataclass
class RouteResult:
    """Complete route result returned to the API layer."""
    start_node: int
    end_node: int
    node_path: list[int]                        # ordered OSM node IDs
    coords: list[tuple[float, float]]           # (lat, lng) for each node
    segments: list[RouteSegment]
    total_distance_m: float
    total_time_s: float                         # sum of composite weights
    free_flow_time_s: float                     # base time without any penalties
    signal_nodes_on_route: list[int]            # for Stage 4 signal module
    hazard_edges_on_route: list[tuple[int,int,int]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in metres between two (lat, lon) points.
    Uses the Haversine formula — accurate to within 0.5% for distances
    under 1000 km, which is more than sufficient for city-scale routing.
    """
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _build_weight_callable(
    signal_nodes: frozenset[int],
    hazard_edges: frozenset[tuple],
) -> callable:
    """
    Return a weight function with signature (u, v, data) → float that
    NetworkX's astar_path accepts.

    We use a closure so the signal_nodes and hazard_edges sets (which are
    computed once at graph load time and never change) are captured by
    reference without being recomputed on every edge evaluation.

    The congestion_store is read at call time (not captured) so it always
    reflects the latest injections.
    """
    def weight_fn(u: int, v: int, data: dict) -> float:
        return compute_edge_weight(
            u, v, data,
            congestion_store=get_store(),
            signal_nodes=signal_nodes,
            hazard_edges=hazard_edges,
            # For MultiDiGraph, NetworkX calls this for each parallel edge
            # and uses the minimum.  We don't have the key here, so default to 0.
            key=0,
        )
    return weight_fn


def _make_heuristic(G: nx.MultiDiGraph):
    """
    Return the A* heuristic: h(u, goal) = Haversine(u, goal) / max_speed.

    The division by max_speed converts distance to a time lower-bound,
    keeping units consistent with the composite weight (seconds).
    """
    def heuristic(u: int, goal: int) -> float:
        lat1 = G.nodes[u]["y"]
        lon1 = G.nodes[u]["x"]
        lat2 = G.nodes[goal]["y"]
        lon2 = G.nodes[goal]["x"]
        return _haversine_m(lat1, lon1, lat2, lon2) / _MAX_SPEED_MS
    return heuristic


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_route(
    G: nx.MultiDiGraph,
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
) -> RouteResult:
    """
    Compute the composite-weight-optimal route from (start_lat, start_lng)
    to (end_lat, end_lng) on the road graph G.

    Steps
    -----
    1. Snap the input coordinates to the nearest graph nodes using OSMnx.
    2. Pre-compute the signal-node and hazard-edge sets (cached on graph).
    3. Run A* with the composite weight function and Haversine heuristic.
    4. Extract per-segment statistics for the frontend and signal module.
    5. Return a RouteResult with everything the API layer needs.

    Raises
    ------
    nx.NetworkXNoPath   — if no path exists between the snapped nodes
    ValueError          — if coordinates are outside the graph's bounding box
    """
    # ── 1. Snap coordinates to nearest graph nodes ──────────────────────────
    start_node = ox.nearest_nodes(G, X=start_lng, Y=start_lat)
    end_node   = ox.nearest_nodes(G, X=end_lng,   Y=end_lat)

    logger.info(
        "Routing: (%.5f, %.5f) → node %d | (%.5f, %.5f) → node %d",
        start_lat, start_lng, start_node, end_lat, end_lng, end_node,
    )

    # ── 2. Precompute static sets ────────────────────────────────────────────
    signal_nodes = get_signal_nodes(G)
    hazard_edges = get_hazard_edges(G)

    # ── 3. A* shortest path ──────────────────────────────────────────────────
    weight_fn  = _build_weight_callable(signal_nodes, hazard_edges)
    heuristic  = _make_heuristic(G)

    node_path: list[int] = nx.astar_path(
        G,
        source=start_node,
        target=end_node,
        heuristic=heuristic,
        weight=weight_fn,
    )

    logger.info("Route found: %d nodes, computing segment details…", len(node_path))

    # ── 4. Extract per-segment statistics ────────────────────────────────────
    segments: list[RouteSegment] = []
    total_dist = 0.0
    total_time = 0.0
    free_flow_time = 0.0
    signal_on_route: list[int] = []
    hazard_on_route: list[tuple] = []

    for i in range(len(node_path) - 1):
        u, v = node_path[i], node_path[i + 1]

        # Pick the best (lowest-weight) parallel edge
        best_key = min(
            G[u][v],
            key=lambda k: weight_fn(u, v, G[u][v][k]),
        )
        data = G[u][v][best_key]

        # Recompute individual terms for the segment record
        from .graph import _edge_speed_kph, _SIGNAL_WAIT_S, _HAZARD_PENALTY_S
        speed_kph = _edge_speed_kph(data)
        speed_ms  = speed_kph / 3.6
        length_m  = float(data.get("length", 0))
        base_s    = length_m / speed_ms
        cong      = get_store().get((u, v, best_key), get_store().get((u, v, 0), 1.0))
        is_sig    = v in signal_nodes
        is_haz    = (u, v, best_key) in hazard_edges
        composite = base_s * cong + (_SIGNAL_WAIT_S if is_sig else 0) + (_HAZARD_PENALTY_S if is_haz else 0)

        name = data.get("name", "")
        if isinstance(name, list):
            name = name[0] if name else ""

        hw = data.get("highway", "")
        if isinstance(hw, list):
            hw = hw[0] if hw else ""

        segments.append(RouteSegment(
            u=u, v=v, k=best_key,
            length_m=length_m,
            name=str(name),
            highway=str(hw),
            speed_kph=speed_kph,
            base_time_s=base_s,
            congestion_factor=cong,
            is_signal_at_dest=is_sig,
            is_hazard=is_haz,
            composite_weight_s=composite,
        ))

        total_dist    += length_m
        total_time    += composite
        free_flow_time += base_s

        if is_sig and v not in signal_on_route:
            signal_on_route.append(v)
        if is_haz:
            hazard_on_route.append((u, v, best_key))

    # ── 5. Extract coordinates — including intermediate geometry points ────────
    #
    # OSMnx edges can store a Shapely LineString in data["geometry"] that
    # contains the road's actual shape: curves, flyovers, roundabout arcs, etc.
    # If we only take one (lat, lng) per node (intersection), the polyline
    # draws straight lines between intersections — which is wrong for curved
    # roads like flyover ramps or curved arterials.
    #
    # Fix: for each edge (u→v), extract all intermediate points from the
    # LineString geometry.  The LineString coordinates are (lng, lat) in OSMnx
    # convention (x=lng, y=lat), so we must flip them to (lat, lng) for Leaflet.
    #
    # If an edge has no "geometry" attribute (a straight segment in OSM), we
    # fall back to just the start-node coordinate for that edge, and the
    # end-node coordinate is added as the final point after the loop.
    coords: list[tuple[float, float]] = []

    for i in range(len(node_path) - 1):
        u, v = node_path[i], node_path[i + 1]
        best_key = segments[i].k
        data = G[u][v][best_key]

        if "geometry" in data:
            # Extract all intermediate points from the Shapely LineString.
            # coords() returns (x, y) = (lng, lat) — flip to (lat, lng).
            geom_coords = list(data["geometry"].coords)

            # The LineString may be stored in either u→v or v→u direction.
            # Check by comparing the first geometry point with node u.
            u_lat = G.nodes[u]["y"]
            u_lng = G.nodes[u]["x"]
            first_lng, first_lat = geom_coords[0]

            import math as _math
            dist_fwd = _math.hypot(first_lat - u_lat, first_lng - u_lng)
            dist_rev = _math.hypot(first_lat - G.nodes[v]["y"], first_lng - G.nodes[v]["x"])

            if dist_rev < dist_fwd:
                # Geometry is stored v→u; reverse it so it reads u→v
                geom_coords = geom_coords[::-1]

            # Add all points except the last (which is v's position — added
            # as the first point of the next edge, or as the final point below)
            for lng, lat in geom_coords[:-1]:
                coords.append((lat, lng))
        else:
            # No geometry attribute — straight segment; just add the start node
            coords.append((G.nodes[u]["y"], G.nodes[u]["x"]))

    # Always append the final destination node
    coords.append((G.nodes[node_path[-1]]["y"], G.nodes[node_path[-1]]["x"]))

    return RouteResult(
        start_node=start_node,
        end_node=end_node,
        node_path=node_path,
        coords=coords,
        segments=segments,
        total_distance_m=total_dist,
        total_time_s=total_time,
        free_flow_time_s=free_flow_time,
        signal_nodes_on_route=signal_on_route,
        hazard_edges_on_route=hazard_on_route,
    )
