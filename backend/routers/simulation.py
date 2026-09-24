"""
routers/simulation.py — Ambulance simulation API + WebSocket
=============================================================

Endpoints
---------
POST   /sim/start                — create and start a new ambulance simulation
POST   /sim/{id}/reroute         — inject congestion and trigger live reroute
POST   /sim/{id}/cancel          — stop a running simulation
GET    /sim/list                 — list all active simulations
WS     /sim/{id}/ws              — real-time telemetry stream (WebSocket)

Flow
----
1. Frontend calls POST /sim/start with start/end coordinates + ambulance ID.
2. Backend computes the route, creates an AmbulanceSimulation, registers it.
3. Frontend opens a WebSocket to /sim/{id}/ws.
4. Backend pushes TelemetryPing JSON every `tick_interval` seconds.
5. When congestion is injected (via the /congestion/inject endpoint or the
   /sim/{id}/reroute convenience endpoint), the backend recomputes the route
   and calls sim.reroute() — the ambulance path updates live mid-stream.

WebSocket protocol
------------------
Server → Client messages are JSON objects with a "type" field:

  {"type": "telemetry", "data": { ...TelemetryPing fields... }}
  {"type": "reroute",   "data": { ...new route coords + reason... }}
  {"type": "arrived",   "data": { ...final stats... }}
  {"type": "error",     "data": { "message": "..." }}

Client → Server messages (optional):
  {"type": "reroute"}   — request an immediate reroute check
  {"type": "cancel"}    — stop the simulation

Design note — why WebSocket instead of SSE?
FastAPI has better WebSocket support than SSE (Server-Sent Events).
WebSockets are bidirectional, which lets the frontend send reroute triggers
or cancellation requests without a separate HTTP call.  SSE would require
a separate POST endpoint for every client→server action.
"""

import asyncio
import logging
import time
from dataclasses import asdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from pydantic import BaseModel, Field

import networkx as nx

from services.simulator import (
    AmbulanceSimulation,
    SimulationState,
    get_simulation,
    register_simulation,
    unregister_simulation,
    list_simulations,
)
from services.router import compute_route
from services import congestion as congestion_svc
from services.signals import get_corridor, reset_corridor
from services.hospitals import (
    get_alert_manager,
    get_hospital,
    find_nearest_hospital,
    HOSPITAL_REGISTRY,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sim", tags=["simulation"])

# ---------------------------------------------------------------------------
# Graph dependency — same pattern as route.py
# ---------------------------------------------------------------------------
_graph: nx.MultiDiGraph | None = None


def set_graph(G: nx.MultiDiGraph) -> None:
    global _graph
    _graph = G


def _get_graph() -> nx.MultiDiGraph:
    if _graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Road network graph is still loading.",
        )
    return _graph


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class StartSimRequest(BaseModel):
    """Start a new ambulance simulation."""
    ambulance_id: str = Field("AMB-001", description="Unique ambulance identifier")
    start_lat: float  = Field(..., ge=-90,  le=90,  example=16.5062)
    start_lng: float  = Field(..., ge=-180, le=180, example=80.6480)
    end_lat: float    = Field(..., ge=-90,  le=90,  example=16.5193)
    end_lng: float    = Field(..., ge=-180, le=180, example=80.6305)
    severity: str     = Field("critical", description="Case severity: critical, serious, moderate")
    hospital_id: str  = Field(
        "govt-gen-hosp",
        description=(
            "Destination hospital ID from the registry. "
            "Pre-alerts are sent to this hospital as the ambulance approaches. "
            "Use GET /hospitals to see all available hospital IDs."
        ),
    )
    speed_multiplier: float = Field(
        3.0, ge=0.5, le=20.0,
        description=(
            "Demo speed factor. 1.0 = real-time, 3.0 = 3x faster (good for demos). "
            "Does not affect the underlying physics model or ETA calculations — "
            "only how fast the simulation clock runs."
        ),
    )
    tick_interval_s: float = Field(
        0.5, ge=0.1, le=5.0,
        description="Seconds between telemetry pings. 0.5 = 2 updates/sec.",
    )


class RerouteRequest(BaseModel):
    """Inject congestion and trigger reroute for a running simulation."""
    lat: float        = Field(..., ge=-90,  le=90,  example=16.510)
    lng: float        = Field(..., ge=-180, le=180, example=80.640)
    multiplier: float = Field(5.0, ge=1.0, le=10.0, description="Congestion multiplier")
    radius_m: float   = Field(300, ge=50, le=1000)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.post("/start", summary="Start a new ambulance simulation")
async def start_simulation(req: StartSimRequest):
    """
    Creates a simulation instance and returns the initial route + simulation ID.

    The simulation does NOT begin moving until a WebSocket connection is
    established at /sim/{ambulance_id}/ws.  This two-step flow ensures
    the frontend has the route data to render before movement begins.
    """
    G = _get_graph()

    # Check for duplicate
    existing = get_simulation(req.ambulance_id)
    if existing and not existing.is_finished:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation '{req.ambulance_id}' is already running. Cancel it first.",
        )

    # Compute initial route
    try:
        route_result = compute_route(G, req.start_lat, req.start_lng, req.end_lat, req.end_lng)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Build response matching the /route endpoint format
    route_data = {
        "coords": route_result.coords,
        "node_path": route_result.node_path,
        "segments": [
            {
                "u": s.u, "v": s.v, "k": s.k,
                "length_m": s.length_m,
                "name": s.name,
                "highway": s.highway,
                "speed_kph": s.speed_kph,
                "base_time_s": s.base_time_s,
                "congestion_factor": s.congestion_factor,
                "is_signal_at_dest": s.is_signal_at_dest,
                "is_hazard": s.is_hazard,
                "composite_weight_s": s.composite_weight_s,
            }
            for s in route_result.segments
        ],
        "signal_nodes_on_route": route_result.signal_nodes_on_route,
        "estimated_time_s": route_result.total_time_s,
        "total_distance_m": route_result.total_distance_m,
    }

    # Create and register the simulation
    sim = AmbulanceSimulation(
        ambulance_id=req.ambulance_id,
        route_data=route_data,
        speed_multiplier=req.speed_multiplier,
        severity=req.severity,
    )
    register_simulation(sim)

    # ── Stage 4: Setup signal corridor for this route ─────────────────────
    corridor = get_corridor()
    if route_result.signal_nodes_on_route:
        # Build node coordinate lookup for signal nodes
        sig_coords = {}
        for nid in route_result.signal_nodes_on_route:
            if nid in G.nodes:
                sig_coords[nid] = (G.nodes[nid]["y"], G.nodes[nid]["x"])
        corridor.setup_for_route(
            ambulance_id=req.ambulance_id,
            signal_node_ids=route_result.signal_nodes_on_route,
            node_coords=sig_coords,
        )

    # ── Stage 5: Resolve destination hospital + store on sim ──────────────
    hospital = get_hospital(req.hospital_id)
    if not hospital:
        # Fall back to nearest hospital to the destination coordinates
        hospital = find_nearest_hospital(req.end_lat, req.end_lng)
    # Attach hospital info to the simulation as metadata (not a dataclass field —
    # we use a plain dict to avoid coupling services.simulator to services.hospitals)
    sim._hospital_meta = {
        "hospital_id": hospital.id,
        "hospital_name": hospital.name,
        "lat": hospital.lat,
        "lng": hospital.lng,
        "severity": req.severity,
    }

    return {
        "ambulance_id": req.ambulance_id,
        "state": sim.state.value,
        "route": {
            "coords": route_result.coords,
            "total_distance_m": round(route_result.total_distance_m, 1),
            "total_distance_km": round(route_result.total_distance_m / 1000, 3),
            "estimated_time_s": round(route_result.total_time_s, 1),
            "free_flow_time_s": round(route_result.free_flow_time_s, 1),
            "signal_nodes_on_route": route_result.signal_nodes_on_route,
            "segment_count": len(route_result.segments),
            "node_count": len(route_result.node_path),
        },
        # Stage 5: hospital info returned so frontend can show it immediately
        "hospital": {
            "id": hospital.id,
            "name": hospital.name,
            "short_name": hospital.short_name,
            "lat": hospital.lat,
            "lng": hospital.lng,
            "type": hospital.type,
            "trauma_bays": hospital.trauma_bays,
            "icu_beds": hospital.icu_beds,
            "blood_bank": hospital.blood_bank,
            "cath_lab": hospital.cath_lab,
        },
        "tick_interval_s": req.tick_interval_s,
        "speed_multiplier": req.speed_multiplier,
        "message": (
            f"Simulation '{req.ambulance_id}' created. "
            f"Destination hospital: {hospital.name}. "
            f"Connect to ws://localhost:8000/sim/{req.ambulance_id}/ws to start receiving telemetry."
        ),
    }


@router.post("/{ambulance_id}/reroute", summary="Inject congestion and reroute")
async def reroute_simulation(ambulance_id: str, req: RerouteRequest):
    """
    Convenience endpoint that:
    1. Injects congestion at the given point (same as POST /congestion/inject)
    2. Recomputes the route from the ambulance's current position
    3. Splices the new route into the running simulation

    This is the endpoint to call during a demo to show live rerouting.
    """
    G = _get_graph()

    sim = get_simulation(ambulance_id)
    if not sim or sim.is_finished:
        raise HTTPException(404, f"No active simulation '{ambulance_id}'.")

    # 1. Inject congestion
    affected = congestion_svc.inject_near_point(G, req.lat, req.lng, req.multiplier, req.radius_m)

    # 2. Recompute route from current position to original destination
    cur_lat, cur_lng = sim.current_position()
    dest_lat, dest_lng = sim.coords[-1]

    try:
        new_route_result = compute_route(G, cur_lat, cur_lng, dest_lat, dest_lng)
    except Exception as exc:
        raise HTTPException(422, f"Reroute failed: {exc}")

    # Build route data dict for the simulation
    new_route_data = {
        "coords": new_route_result.coords,
        "node_path": new_route_result.node_path,
        "segments": [
            {
                "u": s.u, "v": s.v, "k": s.k,
                "length_m": s.length_m,
                "name": s.name,
                "highway": s.highway,
                "speed_kph": s.speed_kph,
                "base_time_s": s.base_time_s,
                "congestion_factor": s.congestion_factor,
                "is_signal_at_dest": s.is_signal_at_dest,
                "is_hazard": s.is_hazard,
                "composite_weight_s": s.composite_weight_s,
            }
            for s in new_route_result.segments
        ],
        "signal_nodes_on_route": new_route_result.signal_nodes_on_route,
        "estimated_time_s": new_route_result.total_time_s,
        "total_distance_m": new_route_result.total_distance_m,
    }

    # 3. Splice the new route into the simulation
    sim.reroute(new_route_data)

    return {
        "ambulance_id": ambulance_id,
        "reroute_count": sim.reroute_count,
        "congestion_injected": len(affected),
        "new_route": {
            "coords": new_route_result.coords,
            "total_distance_m": round(new_route_result.total_distance_m, 1),
            "estimated_time_s": round(new_route_result.total_time_s, 1),
            "segment_count": len(new_route_result.segments),
        },
        "message": f"Reroute #{sim.reroute_count} complete.",
    }


@router.post("/{ambulance_id}/cancel", summary="Cancel a running simulation")
async def cancel_simulation(ambulance_id: str):
    sim = get_simulation(ambulance_id)
    if not sim:
        raise HTTPException(404, f"No simulation '{ambulance_id}'.")
    sim.cancel()
    return {"ambulance_id": ambulance_id, "state": sim.state.value}


@router.get("/list", summary="List all active simulations")
async def list_sims():
    sims = list_simulations()
    return {"count": len(sims), "simulations": sims}


# ---------------------------------------------------------------------------
# WebSocket — real-time telemetry stream
# ---------------------------------------------------------------------------

@router.websocket("/{ambulance_id}/ws")
async def simulation_websocket(websocket: WebSocket, ambulance_id: str):
    """
    Real-time telemetry stream for a simulation.

    Protocol:
      - Server sends JSON messages with "type" field (telemetry / reroute / arrived / error)
      - Client can send {"type": "cancel"} to stop the simulation

    The tick loop runs at the simulation's configured tick_interval.
    Each tick advances the ambulance by (tick_interval × speed_multiplier)
    seconds of simulated time and pushes a telemetry ping.

    Why tick_interval and speed_multiplier are separate:
      tick_interval controls network update rate (how often the frontend gets data).
      speed_multiplier controls simulation speed (how fast the ambulance moves).
      A 0.5s tick with 3x speed → smooth 2Hz updates, ambulance moves 3x faster
      than real-time — ideal for a live demo.
    """
    await websocket.accept()
    logger.info("WebSocket connected: %s", ambulance_id)

    sim = get_simulation(ambulance_id)
    if not sim:
        await websocket.send_json({
            "type": "error",
            "data": {"message": f"No simulation '{ambulance_id}'. Call POST /sim/start first."},
        })
        await websocket.close()
        return

    # Start the simulation (transitions from WAITING → RUNNING)
    sim.start()

    # Determine tick interval from the start request (stored on the sim? no, passed via query or default)
    # For now, use 0.5s default.  The start endpoint can set this.
    tick_interval = 0.5

    # Parse tick_interval from query params if provided
    tick_param = websocket.query_params.get("tick", "0.5")
    try:
        tick_interval = max(0.1, min(5.0, float(tick_param)))
    except ValueError:
        pass

    prev_reroute_count = sim.reroute_count
    last_route_coords = list(sim.coords)

    try:
        while not sim.is_finished:
            # Advance the simulation
            ping = sim.tick(dt=tick_interval)

            if ping is None:
                break

            # ── Stage 4: Update signal corridor with current telemetry ────
            # The corridor controller uses the ambulance's position, speed, and
            # heading to: (1) request priority at upcoming signals, (2) release
            # priority at signals the ambulance has passed, (3) tick all signal
            # state machines forward by dt seconds.
            corridor = get_corridor()
            signal_snapshots = corridor.update(ping, dt=tick_interval)
            signal_data = [
                {
                    "node_id": s.node_id,
                    "lat": s.lat,
                    "lng": s.lng,
                    "mode": s.mode,
                    "phase": s.phase,
                    "phase_elapsed_s": s.phase_elapsed_s,
                    "phase_remaining_s": s.phase_remaining_s,
                    "ambulance_direction": s.ambulance_direction,
                    "cross_direction": s.cross_direction,
                    "priority_ambulance_id": s.priority_ambulance_id,
                    "priority_eta_s": s.priority_eta_s,
                    "time_saved_s": s.time_saved_s,
                }
                for s in signal_snapshots
            ]

            # Send telemetry + signal states as a single atomic message
            await websocket.send_json({
                "type": "telemetry",
                "data": {
                    "ambulance_id": ping.ambulance_id,
                    "lat": ping.lat,
                    "lng": ping.lng,
                    "speed_kmh": ping.speed_kmh,
                    "heading_deg": ping.heading_deg,
                    "distance_remaining_m": ping.distance_remaining_m,
                    "distance_covered_m": ping.distance_covered_m,
                    "eta_seconds": ping.eta_seconds,
                    "current_segment_index": ping.current_segment_index,
                    "total_segments": ping.total_segments,
                    "state": ping.state,
                    "timestamp": ping.timestamp,
                    "current_road_name": ping.current_road_name,
                    "congestion_factor": ping.congestion_factor,
                    "is_near_signal": ping.is_near_signal,
                    "signal_node_id": ping.signal_node_id,
                    "reroute_count": ping.reroute_count,
                    # Stage 4: signal states inline with telemetry
                    "signals": signal_data,
                },
            })

            # ── Stage 5: Hospital pre-alert check ─────────────────────────
            # Each tick we check whether the ambulance has crossed one of the
            # three alert thresholds (2km, 1km, 50m from destination hospital).
            # check_and_alert() is idempotent — each threshold fires at most once.
            hosp_meta = getattr(sim, "_hospital_meta", None)
            if hosp_meta:
                alert_event = get_alert_manager().check_and_alert(
                    ambulance_id=sim.ambulance_id,
                    severity=hosp_meta["severity"],
                    hospital_id=hosp_meta["hospital_id"],
                    hospital_lat=hosp_meta["lat"],
                    hospital_lng=hosp_meta["lng"],
                    amb_lat=ping.lat,
                    amb_lng=ping.lng,
                    eta_s=ping.eta_seconds,
                )
                if alert_event:
                    # Push a dedicated "alert" message so the frontend can
                    # show a notification popup and update the alert panel.
                    await websocket.send_json({
                        "type": "alert",
                        "data": alert_event,
                    })

            # If a reroute happened since last tick, send the new route coords
            if sim.reroute_count > prev_reroute_count:
                await websocket.send_json({
                    "type": "reroute",
                    "data": {
                        "reroute_count": sim.reroute_count,
                        "new_coords": list(sim.coords),
                        "message": f"Reroute #{sim.reroute_count} - path updated.",
                    },
                })
                prev_reroute_count = sim.reroute_count
                last_route_coords = list(sim.coords)

            # Check for arrived
            if sim.state == SimulationState.ARRIVED:
                corridor_stats = get_corridor().get_stats()

                # Stage 7: Calculate actual wall-clock elapsed time.
                # Because the simulator runs at speed_multiplier × real-time, the
                # actual human-perceived "demo elapsed" is finished_at - started_at.
                # The "real-world equivalent" is elapsed × speed_multiplier — that's
                # the simulated time the ambulance would have taken in real-time physics.
                elapsed_wall_s = sim.finished_at - sim.started_at
                real_world_equiv_s = elapsed_wall_s * sim.speed_multiplier

                await websocket.send_json({
                    "type": "arrived",
                    "data": {
                        "ambulance_id": sim.ambulance_id,
                        "total_distance_m": round(sim._distance_covered_m, 1),
                        "reroute_count": sim.reroute_count,
                        "route_history": sim.route_history,
                        # Stage 4: signal priority summary for "before vs after" metrics
                        "signal_stats": corridor_stats,
                        # Stage 7: timing data for before/after comparison
                        "timing": {
                            "elapsed_wall_s":         round(elapsed_wall_s, 1),
                            "real_world_equiv_s":     round(real_world_equiv_s, 1),
                            "initial_estimated_time_s": round(sim.initial_estimated_time_s, 1),
                            "speed_multiplier":       sim.speed_multiplier,
                            "initial_total_distance_m": round(sim.initial_total_distance_m, 1),
                        },
                    },
                })
                break

            # Listen for client messages (non-blocking)
            try:
                # Use wait_for so we don't block the tick loop
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=tick_interval)
                import json
                msg = json.loads(raw)
                if msg.get("type") == "cancel":
                    sim.cancel()
                    break
                elif msg.get("type") == "reroute":
                    # Client-requested reroute (re-uses current congestion state)
                    G = _get_graph()
                    cur_lat, cur_lng = sim.current_position()
                    dest_lat, dest_lng = sim.coords[-1]
                    new_result = compute_route(G, cur_lat, cur_lng, dest_lat, dest_lng)
                    new_data = {
                        "coords": new_result.coords,
                        "node_path": new_result.node_path,
                        "segments": [
                            {
                                "u": s.u, "v": s.v, "k": s.k,
                                "length_m": s.length_m,
                                "name": s.name,
                                "highway": s.highway,
                                "speed_kph": s.speed_kph,
                                "base_time_s": s.base_time_s,
                                "congestion_factor": s.congestion_factor,
                                "is_signal_at_dest": s.is_signal_at_dest,
                                "is_hazard": s.is_hazard,
                                "composite_weight_s": s.composite_weight_s,
                            }
                            for s in new_result.segments
                        ],
                        "signal_nodes_on_route": new_result.signal_nodes_on_route,
                        "estimated_time_s": new_result.total_time_s,
                        "total_distance_m": new_result.total_distance_m,
                    }
                    sim.reroute(new_data)
            except asyncio.TimeoutError:
                # No client message within tick_interval — normal, continue ticking
                pass

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", ambulance_id)
    except Exception as exc:
        logger.exception("WebSocket error for %s", ambulance_id)
        try:
            await websocket.send_json({
                "type": "error",
                "data": {"message": str(exc)},
            })
        except Exception:
            pass
    finally:
        # Clean up the simulation if the client disconnected
        if sim.state == SimulationState.RUNNING:
            sim.cancel()
        unregister_simulation(ambulance_id)
        logger.info("Simulation %s cleaned up", ambulance_id)
