"""
trips.py — persistent trip history (one row per ambulance run)
"""

import time

import db


def start_trip(org_id: int, unit_id: str, hospital_id, hospital_name, emergency_type, severity) -> int:
    with db.connect() as conn:
        return conn.execute(
            "INSERT INTO trips (org_id, unit_id, hospital_id, hospital_name, emergency_type, severity, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (org_id, unit_id, hospital_id, hospital_name, emergency_type, severity, time.time()),
        ).lastrowid


def finish_trip(trip_id: int, status: str, **m) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE trips SET status = ?, ended_at = ?, distance_m = ?, duration_s = ?, estimated_s = ?, "
            "reroute_count = ?, preemptions = ?, signal_saved_s = ? WHERE id = ? AND status = 'active'",
            (status, time.time(), m.get("distance_m"), m.get("duration_s"), m.get("estimated_s"),
             m.get("reroute_count"), m.get("preemptions"), m.get("signal_saved_s"), trip_id),
        )


def list_trips(org_id: int, limit: int = 100) -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trips WHERE org_id = ? ORDER BY started_at DESC LIMIT ?", (org_id, limit)
        ).fetchall()
        s = conn.execute(
            "SELECT COUNT(*) n, "
            "SUM(status = 'completed') done, "
            "AVG(CASE WHEN status = 'completed' THEN duration_s END) avg_duration_s, "
            "SUM(CASE WHEN status = 'completed' THEN signal_saved_s END) signal_saved_s, "
            "SUM(CASE WHEN status = 'completed' THEN reroute_count END) reroutes, "
            "SUM(CASE WHEN status = 'completed' THEN distance_m END) distance_m "
            "FROM trips WHERE org_id = ?", (org_id,)
        ).fetchone()
    return {"summary": dict(s), "trips": [dict(r) for r in rows]}
