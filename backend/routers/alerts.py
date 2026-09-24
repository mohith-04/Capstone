"""
routers/alerts.py — Hospital Pre-Alert API endpoints
=====================================================

Endpoints
---------
GET    /alerts                      — list all alerts (all statuses)
GET    /alerts/{alert_id}           — get one alert by ID
POST   /alerts/{alert_id}/ack       — hospital acknowledges alert
GET    /hospitals                   — list hospital registry
GET    /hospitals/{hospital_id}     — get one hospital
POST   /hospitals/nearest           — find nearest hospital to coordinates
POST   /alerts/reset                — clear all alerts (between demo runs)

Real-time alert events (initial, update, arrival) are pushed inline with the
simulation WebSocket telemetry — see routers/simulation.py.  This router
provides:
  - The REST surface for the hospital-side UI (acknowledging alerts)
  - The registry for the frontend to show hospital markers on the map
  - Diagnostic/admin endpoints for demo operations
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.hospitals import (
    get_alert_manager,
    reset_alert_manager,
    get_all_hospitals,
    get_hospital,
    find_nearest_hospital,
    AlertStatus,
    FIRST_ALERT_DISTANCE_M,
    ETA_UPDATE_DISTANCE_M,
    ARRIVAL_DISTANCE_M,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["alerts"])


# ============================================================================
# Request models
# ============================================================================

class NearestHospitalRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, example=16.5193)
    lng: float = Field(..., ge=-180, le=180, example=80.6305)
    hospital_type: Optional[str] = Field(
        None,
        description="Filter by type: 'trauma_centre', 'general', 'cardiac', 'children'",
    )


# ============================================================================
# Hospital registry endpoints
# ============================================================================

@router.get("/hospitals", summary="List all hospitals in the registry")
async def list_hospitals():
    """
    Returns the full hospital registry with capabilities and coordinates.

    The frontend uses this to place hospital markers on the Leaflet map.
    """
    hospitals = get_all_hospitals()
    return {
        "count": len(hospitals),
        "hospitals": [
            {
                "id": h.id,
                "name": h.name,
                "short_name": h.short_name,
                "lat": h.lat,
                "lng": h.lng,
                "type": h.type,
                "trauma_bays": h.trauma_bays,
                "icu_beds": h.icu_beds,
                "ot_count": h.ot_count,
                "blood_bank": h.blood_bank,
                "cath_lab": h.cath_lab,
                "phone": h.phone,
                "emergency_phone": h.emergency_phone,
            }
            for h in hospitals
        ],
    }


@router.get("/hospitals/{hospital_id}", summary="Get one hospital by ID")
async def get_hospital_by_id(hospital_id: str):
    hospital = get_hospital(hospital_id)
    if not hospital:
        raise HTTPException(404, f"Hospital '{hospital_id}' not found.")
    return {
        "id": hospital.id,
        "name": hospital.name,
        "short_name": hospital.short_name,
        "lat": hospital.lat,
        "lng": hospital.lng,
        "type": hospital.type,
        "trauma_bays": hospital.trauma_bays,
        "icu_beds": hospital.icu_beds,
        "ot_count": hospital.ot_count,
        "blood_bank": hospital.blood_bank,
        "cath_lab": hospital.cath_lab,
        "phone": hospital.phone,
        "emergency_phone": hospital.emergency_phone,
    }


@router.post("/hospitals/nearest", summary="Find nearest hospital to a location")
async def nearest_hospital(req: NearestHospitalRequest):
    """
    Find the nearest hospital to the given coordinates.

    Used by the frontend when the user picks a destination on the map —
    it auto-suggests the nearest appropriate hospital based on case type.
    """
    hospital = find_nearest_hospital(req.lat, req.lng, req.hospital_type)
    from services.hospitals import _haversine_m
    dist_m = _haversine_m(req.lat, req.lng, hospital.lat, hospital.lng)
    return {
        "id": hospital.id,
        "name": hospital.name,
        "short_name": hospital.short_name,
        "lat": hospital.lat,
        "lng": hospital.lng,
        "type": hospital.type,
        "distance_m": round(dist_m, 1),
        "distance_km": round(dist_m / 1000, 2),
        "trauma_bays": hospital.trauma_bays,
        "icu_beds": hospital.icu_beds,
        "blood_bank": hospital.blood_bank,
        "cath_lab": hospital.cath_lab,
    }


# ============================================================================
# Alert endpoints
# ============================================================================

@router.get("/alerts", summary="List all hospital alerts")
async def list_alerts(status: Optional[str] = None):
    """
    Return all alerts, optionally filtered by status.

    status filter values: pending, sent, updated, acknowledged, arrived
    """
    mgr = get_alert_manager()
    alerts = mgr.get_all_alerts()

    if status:
        try:
            filter_status = AlertStatus(status)
            alerts = [a for a in alerts if a.status == filter_status]
        except ValueError:
            raise HTTPException(400, f"Invalid status '{status}'. Valid values: {[s.value for s in AlertStatus]}")

    return {
        "count": len(alerts),
        "alerts": [_serialize_alert(a) for a in alerts],
    }


@router.get("/alerts/{alert_id}", summary="Get one alert by ID")
async def get_alert(alert_id: str):
    mgr = get_alert_manager()
    alert = mgr.get_alert(alert_id)
    if not alert:
        raise HTTPException(404, f"Alert '{alert_id}' not found.")
    return _serialize_alert(alert)


@router.post("/alerts/{alert_id}/ack", summary="Hospital acknowledges pre-alert")
async def acknowledge_alert(alert_id: str):
    """
    Mark an alert as acknowledged by the hospital.

    In a real deployment this would be called by the hospital's own system
    (or a staff member pressing "Acknowledge" in a portal).  For the demo,
    the frontend can call this to simulate the hospital response.
    """
    mgr = get_alert_manager()
    alert = mgr.acknowledge(alert_id)
    if not alert:
        raise HTTPException(404, f"Alert '{alert_id}' not found.")
    return {
        "alert_id": alert_id,
        "status": alert.status.value,
        "acknowledged_at": alert.acknowledged_at,
        "message": f"Alert {alert_id} acknowledged by {alert.hospital_name}.",
    }


@router.post("/alerts/reset", summary="Clear all alerts (demo reset)")
async def reset_alerts():
    """Clear all alerts between demo runs."""
    count = reset_alert_manager()
    return {"cleared": count, "message": f"Cleared {count} alert(s)."}


# ============================================================================
# Helper
# ============================================================================

def _serialize_alert(alert) -> dict:
    """Convert a HospitalAlert to a JSON-serialisable dict."""
    v = alert.vitals
    return {
        "alert_id": alert.alert_id,
        "ambulance_id": alert.ambulance_id,
        "hospital_id": alert.hospital_id,
        "hospital_name": alert.hospital_name,
        "severity": alert.severity,
        "status": alert.status.value,
        "created_at": alert.created_at,
        "dispatched_at": alert.dispatched_at,
        "acknowledged_at": alert.acknowledged_at,
        "arrived_at": alert.arrived_at,
        "initial_eta_s": round(alert.initial_eta_s, 1),
        "current_eta_s": round(alert.current_eta_s, 1),
        "distance_m": round(alert.distance_m, 1),
        "eta_updates": alert.eta_updates,
        "notifications": alert.notifications,
        "patient": {
            "gcs": v.gcs,
            "spo2": v.spo2,
            "hr": v.hr,
            "bp": f"{v.bp_sys}/{v.bp_dia}",
            "rr": v.rr,
            "temp_c": v.temp_c,
            "mechanism": v.mechanism,
            "injuries": v.injuries,
            "treatment_en_route": v.treatment,
            "blood_type": v.blood_type,
            "resources_needed": v.resources_needed,
        },
        # Alert threshold info — useful for the demo explanation
        "_thresholds": {
            "first_alert_m": FIRST_ALERT_DISTANCE_M,
            "eta_update_m": ETA_UPDATE_DISTANCE_M,
            "arrival_m": ARRIVAL_DISTANCE_M,
        },
    }
