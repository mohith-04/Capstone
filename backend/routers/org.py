"""
routers/org.py — hospital dashboard endpoints
=============================================
GET    /org/fleet            org info + every member ambulance with live state
GET    /org/trips            trip history + summary stats
WS     /org/ws?token=JWT     live push feed: fleet snapshots + events
DELETE /org/members/{id}     remove an ambulance from the org
POST   /org/rotate-code      issue a new join code (old one stops working)
"""

import asyncio
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

import auth
import capacity
import db
import fleet
import trips
from pydantic import BaseModel, Field
from services.hospitals import get_all_hospitals, get_hospital

router = APIRouter(prefix="/org", tags=["org"])


def build_fleet(org_id: int) -> dict:
    with db.connect() as conn:
        org = conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
        members = conn.execute(
            "SELECT id, email, unit_id, display_name FROM users "
            "WHERE org_id = ? AND role = 'ambulance' ORDER BY unit_id",
            (org_id,),
        ).fetchall()

    ambulances = []
    for m in members:
        live = fleet.get(m["unit_id"])
        online = live is not None and not live["stale"]
        ambulances.append({
            "id": m["id"],
            "unit_id": m["unit_id"],
            "display_name": m["display_name"],
            "email": m["email"],
            "online": online,
            "live": live if online else None,
        })
    return {
        "org": {"id": org["id"], "name": org["name"], "join_code": org["join_code"]},
        "ambulances": ambulances,
    }


@router.get("/fleet")
def get_fleet(user=Depends(auth.hospital_user)):
    return build_fleet(user["org_id"])


@router.get("/trips")
def get_trips(user=Depends(auth.hospital_user)):
    return trips.list_trips(user["org_id"])


@router.websocket("/ws")
async def org_ws(websocket: WebSocket, token: str = ""):
    """Push feed: a `snapshot` every second plus instant `event` messages."""
    user = auth.user_from_token(token)
    if user is None or user["role"] != "hospital":
        await websocket.close(code=4401)
        return
    await websocket.accept()
    org_id = user["org_id"]
    q = fleet.subscribe(org_id)
    try:
        await websocket.send_json({"type": "alerts", "data": fleet.recent_alerts(org_id)})
        last_snapshot = 0.0
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                await websocket.send_json({"type": "event", "data": event})
                # drain any burst without waiting
                while not q.empty():
                    await websocket.send_json({"type": "event", "data": q.get_nowait()})
            except asyncio.TimeoutError:
                pass
            if time.time() - last_snapshot >= 1.0:
                await websocket.send_json({"type": "snapshot", "data": build_fleet(org_id)})
                last_snapshot = time.time()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        fleet.unsubscribe(org_id, q)


@router.delete("/members/{member_id}")
def remove_member(member_id: int, user=Depends(auth.hospital_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT unit_id FROM users WHERE id = ? AND org_id = ? AND role = 'ambulance'",
            (member_id, user["org_id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Member not found")
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
    fleet.forget_unit(row["unit_id"])
    return {"removed": member_id}


@router.post("/rotate-code")
def rotate_code(user=Depends(auth.hospital_user)):
    code = secrets.token_hex(3).upper()
    with db.connect() as conn:
        conn.execute("UPDATE orgs SET join_code = ? WHERE id = ?", (code, user["org_id"]))
    return {"join_code": code}


# ── Capacity reporting ───────────────────────────────────────────────────────

class CapacityUpdate(BaseModel):
    hospital_id: str | None = None           # only needed the first time (claims the hospital)
    er_beds_total: int = Field(..., ge=0, le=500)
    er_beds_available: int = Field(..., ge=0, le=500)
    icu_available: int = Field(..., ge=0, le=500)
    diversion: bool = False
    specialties: list[str] = []


@router.get("/capacity")
def get_capacity(user=Depends(auth.hospital_user)):
    with db.connect() as conn:
        org = conn.execute("SELECT hospital_id FROM orgs WHERE id = ?", (user["org_id"],)).fetchone()
        taken = {r["hospital_id"] for r in conn.execute(
            "SELECT hospital_id FROM orgs WHERE hospital_id IS NOT NULL AND id != ?", (user["org_id"],))}
    hid = org["hospital_id"]
    return {
        "hospital_id": hid,
        "hospital": get_hospital(hid).name if hid else None,
        "status": capacity.get_status(hid) if hid else None,
        "all_specialties": capacity.SPECIALTIES,
        "claimable": [{"id": h.id, "name": h.name} for h in get_all_hospitals() if h.id not in taken],
    }


@router.put("/capacity")
def put_capacity(req: CapacityUpdate, user=Depends(auth.hospital_user)):
    if req.er_beds_available > req.er_beds_total:
        raise HTTPException(422, "Available ER beds cannot exceed total ER beds")
    bad = [s for s in req.specialties if s not in capacity.SPECIALTIES]
    if bad:
        raise HTTPException(422, f"Unknown specialties: {bad}")
    with db.connect() as conn:
        org = conn.execute("SELECT hospital_id FROM orgs WHERE id = ?", (user["org_id"],)).fetchone()
        hid = org["hospital_id"]
        if hid is None:
            if not req.hospital_id or get_hospital(req.hospital_id) is None:
                raise HTTPException(422, "Choose which hospital your organisation represents")
            try:
                conn.execute("UPDATE orgs SET hospital_id = ? WHERE id = ?", (req.hospital_id, user["org_id"]))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "That hospital is already claimed by another organisation")
            hid = req.hospital_id
    capacity.set_status(hid, req.er_beds_total, req.er_beds_available, req.icu_available,
                        req.diversion, req.specialties)
    return get_capacity(user)
