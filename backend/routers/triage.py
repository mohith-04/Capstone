"""
routers/triage.py — capacity-aware hospital recommendation
==========================================================
POST /hospitals/recommend   rank every hospital for this patient and say why
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import capacity
from routers import simulation as sim_router
from services.hospitals import get_all_hospitals
from services.router import compute_route

router = APIRouter(tags=["triage"])


class RecommendRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    emergency_type: str = "Unknown"
    severity: str = "critical"


def _hosp_dict(h):
    return {"id": h.id, "name": h.name, "short_name": h.short_name, "lat": h.lat, "lng": h.lng, "type": h.type}


@router.post("/hospitals/recommend", summary="Rank hospitals by ETA + capacity + specialty")
def recommend(req: RecommendRequest):
    G = sim_router._get_graph()
    rows = []
    for h in get_all_hospitals():
        try:
            r = compute_route(G, req.lat, req.lng, h.lat, h.lng)
        except Exception:
            continue  # unreachable on the loaded graph
        s = capacity.score(h, r.total_time_s, req.emergency_type, req.severity)
        rows.append({
            "hospital": _hosp_dict(h),
            "eta_s": round(r.total_time_s, 1),
            "distance_m": round(r.total_distance_m, 1),
            **s,
        })
    if not rows:
        raise HTTPException(503, "No reachable hospitals")

    nearest = min(rows, key=lambda r: r["eta_s"])
    open_rows = [r for r in rows if not r["excluded"]]
    all_diverted = not open_rows
    best = min(open_rows or rows, key=lambda r: r["cost"])
    ranking = sorted(rows, key=lambda r: (r["excluded"], r["cost"]))
    summary = capacity.explain(best, nearest)
    if all_diverted:
        summary = "All hospitals report diversion — showing the best option anyway. " + summary
    return {
        "recommended_id": best["hospital"]["id"],
        "nearest_id": nearest["hospital"]["id"],
        "changed_from_nearest": best["hospital"]["id"] != nearest["hospital"]["id"],
        "summary": summary,
        "ranking": ranking,
    }
