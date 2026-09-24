"""
routers/health.py — Health-check router
========================================
Provides GET /health so the React frontend can verify backend connectivity
on startup and the demo operator can quickly confirm the server is live.

Returns the server version and a UTC timestamp — useful in a viva to show
that the frontend and backend are genuinely communicating in real time.
"""

from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/", summary="Backend health check")
async def health_check() -> dict:
    """
    Returns a simple liveness payload.

    Fields
    ------
    status      — always "ok" while the server is up
    version     — application version string (matches package metadata)
    timestamp   — UTC ISO-8601 string; lets the frontend confirm clock sync
    message     — human-readable confirmation for demo screenshots
    """
    return {
        "status": "ok",
        "version": "0.5.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "AI Ambulance Route Optimization backend is running.",
    }
