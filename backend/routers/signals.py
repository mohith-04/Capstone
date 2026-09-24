"""
routers/signals.py -- Traffic Signal Priority API endpoints
=============================================================

Endpoints
---------
GET    /signals/status          -- snapshot of all active signal controllers
GET    /signals/stats           -- aggregate statistics (preemptions, time saved)
POST   /signals/reset           -- clear all controllers between demo runs

The real-time signal state updates are NOT served from this router.
Instead, they are pushed as part of the simulation WebSocket stream
(see routers/simulation.py).  This router provides REST endpoints for
the initial page load and for diagnostic/demo purposes.

Why not a separate WebSocket for signals?
The signal states change on every simulation tick and are tightly coupled
to the ambulance's position.  Sending them in the same WebSocket message
as the telemetry ping keeps the frontend's state update atomic -- it
never has to reconcile out-of-order updates from two separate streams.
"""

import logging

from fastapi import APIRouter

from services.signals import get_corridor, reset_corridor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/status", summary="Current state of all signal controllers")
async def signal_status():
    """
    Returns a snapshot of every active IntersectionController.

    Each entry includes:
      - node_id, lat, lng (intersection location)
      - mode (normal / priority_pending / priority_active / restoring)
      - phase (green_A / amber_A / all_red / green_B / amber_B)
      - ambulance_direction / cross_direction (what colour each direction sees)
      - priority_ambulance_id (which ambulance triggered priority, if any)
      - priority_eta_s (ETA of that ambulance)
      - time_saved_s (estimated seconds saved by this preemption)
    """
    corridor = get_corridor()
    snapshots = corridor.get_all_snapshots()
    return {
        "count": len(snapshots),
        "signals": [
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
            for s in snapshots
        ],
    }


@router.get("/stats", summary="Aggregate signal priority statistics")
async def signal_stats():
    """
    Returns aggregate statistics for the current corridor:
      - total intersections controlled
      - total preemptions performed
      - total time saved across all intersections

    These are the numbers used in the "before vs after" comparison in Stage 7.
    """
    corridor = get_corridor()
    return corridor.get_stats()


@router.post("/reset", summary="Reset all signal controllers")
async def signal_reset():
    """
    Clear all IntersectionControllers and corridor state.
    Use between demo runs to start fresh.
    """
    reset_corridor()
    return {"message": "Signal corridor controller reset."}
