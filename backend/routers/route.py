"""
routers/route.py — Routing and congestion API endpoints
=========================================================

Endpoints
---------
POST   /route                   — compute optimal route between two points
GET    /route/graph-info        — basic graph statistics (node/edge count, bbox)
GET    /congestion              — list all active congestion entries
POST   /congestion/inject       — inject congestion near a geographic point
DELETE /congestion/clear        — remove all congestion (reset for new demo run)
DELETE /congestion/edge         — remove congestion on a specific edge

All endpoints depend on the graph singleton loaded at startup (see main.py
lifespan).  If the graph is still loading, a 503 is returned immediately.
"""

import logging
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

import networkx as nx

from services import congestion as congestion_svc
from services.router import compute_route, RouteResult

logger = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])

# ---------------------------------------------------------------------------
# Graph dependency — injected by main.py at startup
# ---------------------------------------------------------------------------
_graph: nx.MultiDiGraph | None = None


def set_graph(G: nx.MultiDiGraph) -> None:
    """Called by main.py after the graph loads; stores the singleton."""
    global _graph
    _graph = G


def _get_graph() -> nx.MultiDiGraph:
    """FastAPI dependency that yields the graph or raises 503."""
    if _graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Road network graph is still loading. Please retry in ~30 seconds.",
        )
    return _graph


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RouteRequest(BaseModel):
    """Geographic coordinates for the routing request."""
    start_lat: float = Field(..., ge=-90, le=90,  example=16.5062, description="Start latitude")
    start_lng: float = Field(..., ge=-180, le=180, example=80.6480, description="Start longitude")
    end_lat:   float = Field(..., ge=-90, le=90,  example=16.5193, description="End latitude")
    end_lng:   float = Field(..., ge=-180, le=180, example=80.6305, description="End longitude")


class CongestionInjectRequest(BaseModel):
    """
    Inject a congestion event near a geographic point.

    multiplier semantics:
      1.0  — free flow (no-op, but allowed)
      2.0  — half speed (heavy traffic)
      5.0  — near-blocked (router will almost certainly reroute around this)
    """
    lat: float        = Field(..., ge=-90, le=90,   example=16.5100)
    lng: float        = Field(..., ge=-180, le=180,  example=80.6420)
    multiplier: float = Field(2.0, ge=1.0, le=10.0, example=2.0,
                              description="Congestion multiplier ≥ 1.0")
    radius_m: float   = Field(200.0, ge=50, le=1000, example=200.0,
                              description="Radius in metres around the point")


class ClearEdgeRequest(BaseModel):
    u: int = Field(..., description="Source node ID")
    v: int = Field(..., description="Destination node ID")
    k: int = Field(0,  description="Parallel-edge key (0 for most edges)")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/route/graph-info", summary="Road graph statistics")
async def graph_info(G: nx.MultiDiGraph = Depends(_get_graph)):
    """
    Returns metadata about the loaded road graph:
    node and edge counts, bounding box, and signal-intersection count.

    Useful as a quick sanity check after startup and in the demo UI.
    """
    from services.graph import get_signal_nodes, get_hazard_edges
    lats = [d["y"] for _, d in G.nodes(data=True)]
    lngs = [d["x"] for _, d in G.nodes(data=True)]
    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "signal_intersections": len(get_signal_nodes(G)),
        "hazard_edges": len(get_hazard_edges(G)),
        "bbox": {
            "south": min(lats), "north": max(lats),
            "west":  min(lngs), "east":  max(lngs),
        },
    }


@router.post("/route", summary="Compute optimal ambulance route")
async def route(
    req: RouteRequest,
    G: nx.MultiDiGraph = Depends(_get_graph),
):
    """
    Compute the composite-weight-optimal route from start to end.

    The route is optimised for total travel time, accounting for:
    - Road speed (from OSM maxspeed tags / road class defaults)
    - Live congestion multipliers on individual edges
    - Signal wait time at signalised intersections
    - Hazard penalties on accident-prone edges

    Returns a GeoJSON-compatible coordinate list plus per-segment
    statistics and ETA estimates.
    """
    t0 = time.perf_counter()
    try:
        result: RouteResult = compute_route(
            G,
            req.start_lat, req.start_lng,
            req.end_lat,   req.end_lng,
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No path found between the given coordinates. "
                   "Both points must lie within the Vijayawada network area.",
        )
    except Exception as exc:
        logger.exception("Routing error")
        raise HTTPException(status_code=500, detail=str(exc))

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        # GeoJSON-style coordinate array for Leaflet Polyline
        "coords": result.coords,                 # [[lat, lng], …]
        "node_path": result.node_path,

        # Summary metrics
        "total_distance_m": round(result.total_distance_m, 1),
        "total_distance_km": round(result.total_distance_m / 1000, 3),
        "estimated_time_s": round(result.total_time_s, 1),
        "free_flow_time_s": round(result.free_flow_time_s, 1),
        "congestion_delay_s": round(result.total_time_s - result.free_flow_time_s, 1),

        # Signal and hazard info for Stage 4 + 6
        "signal_nodes_on_route": result.signal_nodes_on_route,
        "hazard_edges_on_route": [list(e) for e in result.hazard_edges_on_route],

        # Per-segment breakdown (used by frontend for colouring road segments)
        "segments": [asdict(seg) for seg in result.segments],

        # Diagnostics
        "routing_time_ms": round(elapsed_ms, 2),
        "node_count": len(result.node_path),
        "segment_count": len(result.segments),
    }


@router.get("/congestion", summary="List active congestion events")
async def list_congestion():
    """Return all currently active congestion multipliers."""
    entries = congestion_svc.get_all()
    return {
        "count": len(entries),
        "entries": [
            {"u": e.u, "v": e.v, "k": e.k, "multiplier": e.multiplier}
            for e in entries
        ],
    }


@router.post("/congestion/inject", summary="Inject congestion near a point")
async def inject_congestion(
    req: CongestionInjectRequest,
    G: nx.MultiDiGraph = Depends(_get_graph),
):
    """
    Inject a congestion multiplier on road edges near the given (lat, lng).

    This simulates a real-world event (accident, roadblock, festival) that
    slows or blocks traffic on a specific road segment.  After injection,
    the next call to POST /route will automatically route around the affected
    edges if a faster alternative exists.

    Use multiplier ≥ 5.0 to effectively block an edge for demo rerouting.
    """
    affected = congestion_svc.inject_near_point(
        G,
        lat=req.lat,
        lng=req.lng,
        multiplier=req.multiplier,
        radius_m=req.radius_m,
    )
    if not affected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No road edges found within {req.radius_m:.0f} m of "
                   f"({req.lat:.5f}, {req.lng:.5f}).  "
                   "Try a larger radius or a point closer to a road.",
        )
    return {
        "injected": len(affected),
        "edges": [{"u": e.u, "v": e.v, "k": e.k, "multiplier": e.multiplier}
                  for e in affected],
        "message": (
            f"Congestion multiplier {req.multiplier}× injected on "
            f"{len(affected)} edge(s).  Re-call POST /route to see the updated path."
        ),
    }


@router.delete("/congestion/clear", summary="Clear all congestion (reset)")
async def clear_congestion():
    """Remove all active congestion entries. Use this to reset between demo runs."""
    count = congestion_svc.clear_all()
    return {"cleared": count, "message": f"Removed {count} congestion event(s)."}


@router.delete("/congestion/edge", summary="Clear congestion on a specific edge")
async def clear_edge(req: ClearEdgeRequest):
    """Remove congestion from a single edge identified by (u, v, k)."""
    removed = congestion_svc.clear_edge(req.u, req.v, req.k)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"No congestion found on edge ({req.u}, {req.v}, {req.k}).",
        )
    return {"message": f"Congestion cleared on edge ({req.u}, {req.v}, {req.k})."}
