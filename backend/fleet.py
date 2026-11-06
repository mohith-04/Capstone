"""
fleet.py — live fleet state + per-organisation event hub
========================================================
* `publish()` stores the latest telemetry of each ambulance (in memory only —
  it is live data) and emits an "online" event the first time a unit appears.
* `emit()` pushes an event (alert, reroute, arrived, ...) to every hospital
  dashboard connected for that unit's organisation.
* Subscribers are asyncio queues; everything runs on the server's single event
  loop, so no locking is needed for them. `_state` is also read from threadpool
  endpoints, hence the lock.
"""

import asyncio
import collections
import threading
import time

import db

STALE_AFTER_S = 10.0
RECENT_ALERTS = 50

_lock = threading.Lock()
_state: dict[str, dict] = {}
_org_cache: dict[str, int | None] = {}
_subs: dict[int, set[asyncio.Queue]] = collections.defaultdict(set)
_alerts: dict[int, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=RECENT_ALERTS)
)


def org_of(unit_id: str) -> int | None:
    """Organisation that owns this ambulance unit (None for demo/unknown units)."""
    if unit_id not in _org_cache:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT org_id FROM users WHERE unit_id = ? AND role = 'ambulance'", (unit_id,)
            ).fetchone()
        _org_cache[unit_id] = row["org_id"] if row else None
    return _org_cache[unit_id]


def forget_unit(unit_id: str) -> None:
    """Call when a member is removed so stale org lookups don't linger."""
    _org_cache.pop(unit_id, None)
    with _lock:
        _state.pop(unit_id, None)


def subscribe(org_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subs[org_id].add(q)
    return q


def unsubscribe(org_id: int, q: asyncio.Queue) -> None:
    _subs[org_id].discard(q)


def recent_alerts(org_id: int) -> list[dict]:
    return list(_alerts[org_id])


def emit(unit_id: str, kind: str, **fields) -> None:
    org_id = org_of(unit_id)
    if org_id is None:
        return
    event = {"type": kind, "unit_id": unit_id, "ts": time.time(), **fields}
    if kind == "alert":
        _alerts[org_id].appendleft(event)
    for q in list(_subs[org_id]):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow client; it still gets the periodic snapshot


def publish(unit_id: str, data: dict) -> None:
    with _lock:
        is_new = unit_id not in _state
        _state[unit_id] = {**data, "_seen": time.time()}
    if is_new:
        emit(unit_id, "online")


def clear(unit_id: str, emit_event: bool = True) -> None:
    with _lock:
        existed = _state.pop(unit_id, None) is not None
    if existed and emit_event:
        emit(unit_id, "offline")


def get(unit_id: str) -> dict | None:
    with _lock:
        entry = _state.get(unit_id)
    if entry is None:
        return None
    out = {k: v for k, v in entry.items() if k != "_seen"}
    out["stale"] = (time.time() - entry["_seen"]) > STALE_AFTER_S
    return out
