"""
capacity.py — hospital capacity reporting + capacity-aware destination scoring
==============================================================================
Hospitals report ER beds, ICU availability, specialties and diversion status.
`score()` combines that with the *real routed ETA* from the road graph into a
single "cost in minutes" per hospital, so the crew is sent to the right
hospital rather than just the nearest one — and gets the reasons why.

Cost model (lower is better)
----------------------------
cost = ETA_minutes
     + specialty-mismatch penalty   (scaled by case severity)
     + capacity penalty             (no ER bed free / nearly full / no ICU bed)
     + blood-bank penalty           (bleeding cases only)
     + unreported-capacity penalty  (small; prefers hospitals that share data)
A hospital on diversion is excluded unless *every* hospital is on diversion.
All weights are plain constants below so they are easy to explain and tune.
"""

import json
import time

import db
from services.hospitals import Hospital, get_hospital

SPECIALTIES = ["trauma", "cardiac", "stroke", "burns", "obstetrics", "paediatrics", "respiratory", "toxicology"]
STALE_AFTER_S = 6 * 3600   # a report older than 6 h is treated as "not reported"

# What each emergency type needs (anything else = any ER can take it)
NEEDS = {
    "Road Traffic Accident": "trauma",
    "Traumatic Fall": "trauma",
    "Heart Attack / Cardiac Arrest": "cardiac",
    "Stroke": "stroke",
    "Burns": "burns",
    "Respiratory Distress": "respiratory",
    "Poisoning / Overdose": "toxicology",
    "Obstetric Emergency": "obstetrics",
}
BLEEDING = {"Road Traffic Accident", "Traumatic Fall", "Obstetric Emergency"}
ICU_NEEDED = {"critical"}

# ── Tunable weights (minutes of "extra cost") ───────────────────────────────
W_NO_SPECIALTY = {"critical": 20.0, "serious": 8.0, "moderate": 3.0}
W_NO_ER_BED = 12.0
W_ER_NEARLY_FULL = 3.0       # <= 20 % of ER beds free
W_NO_ICU = 5.0               # critical cases only
W_NO_BLOOD = 4.0
W_UNREPORTED = 1.0


def default_specialties(h: Hospital) -> list[str]:
    s = []
    if h.type == "trauma_centre" or h.trauma_bays >= 4:
        s.append("trauma")
    if h.cath_lab:
        s.append("cardiac")
    if h.type == "trauma_centre" or h.icu_beds >= 30:
        s.append("stroke")
    if h.type == "trauma_centre" and h.ot_count >= 6:
        s.append("burns")
    if h.type in ("general", "trauma_centre"):
        s.append("obstetrics")
    if h.type == "children":
        s.append("paediatrics")
    if h.icu_beds >= 15:
        s += ["respiratory", "toxicology"]
    return s


def get_status(hospital_id: str) -> dict:
    """Reported status if fresh, otherwise a derived default flagged reported=False."""
    h = get_hospital(hospital_id)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM hospital_status WHERE hospital_id = ?", (hospital_id,)).fetchone()
    if row and time.time() - row["updated_at"] <= STALE_AFTER_S:
        return {
            "reported": True,
            "er_beds_total": row["er_beds_total"],
            "er_beds_available": row["er_beds_available"],
            "icu_available": row["icu_available"],
            "diversion": bool(row["diversion"]),
            "specialties": json.loads(row["specialties"]),
            "updated_at": row["updated_at"],
        }
    return {
        "reported": False,
        "er_beds_total": h.trauma_bays,
        "er_beds_available": h.trauma_bays,
        "icu_available": h.icu_beds,
        "diversion": False,
        "specialties": default_specialties(h),
        "updated_at": row["updated_at"] if row else None,
    }


def set_status(hospital_id: str, er_total: int, er_avail: int, icu_avail: int,
               diversion: bool, specialties: list[str]) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO hospital_status (hospital_id, er_beds_total, er_beds_available, icu_available, "
            "diversion, specialties, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(hospital_id) DO UPDATE SET er_beds_total=excluded.er_beds_total, "
            "er_beds_available=excluded.er_beds_available, icu_available=excluded.icu_available, "
            "diversion=excluded.diversion, specialties=excluded.specialties, updated_at=excluded.updated_at",
            (hospital_id, er_total, er_avail, icu_avail, int(diversion), json.dumps(specialties), time.time()),
        )


def score(h: Hospital, eta_s: float, emergency_type: str, severity: str) -> dict:
    """Cost + human-readable reasons for one hospital."""
    st = get_status(h.id)
    cost = eta_s / 60.0
    reasons: list[str] = []          # things that count against it
    pros: list[str] = []

    need = NEEDS.get(emergency_type)
    if need:
        if need in st["specialties"]:
            pros.append(f"has {need} care")
        else:
            cost += W_NO_SPECIALTY.get(severity, W_NO_SPECIALTY["serious"])
            reasons.append(f"no {need} specialty")

    avail, total = st["er_beds_available"], max(st["er_beds_total"], 1)
    if avail <= 0:
        cost += W_NO_ER_BED
        reasons.append("ER full (0 beds)")
    elif avail / total <= 0.2:
        cost += W_ER_NEARLY_FULL
        reasons.append(f"ER nearly full ({avail}/{total} beds)")
    else:
        pros.append(f"{avail}/{total} ER beds free")

    if severity in ICU_NEEDED and st["icu_available"] <= 0:
        cost += W_NO_ICU
        reasons.append("no ICU bed free")

    if emergency_type in BLEEDING and not h.blood_bank:
        cost += W_NO_BLOOD
        reasons.append("no blood bank")

    if not st["reported"]:
        cost += W_UNREPORTED
        reasons.append("capacity not reported")

    return {
        "cost": round(cost, 2),
        "excluded": st["diversion"],
        "reasons": (["on DIVERSION"] if st["diversion"] else []) + reasons,
        "pros": pros,
        "status": st,
    }


def explain(best: dict, nearest: dict) -> str:
    """One-sentence reason shown to the crew."""
    b, n = best["hospital"]["short_name"], nearest["hospital"]["short_name"]
    if best["hospital"]["id"] == nearest["hospital"]["id"]:
        gaps = [r for r in best["reasons"] if r != "capacity not reported"]
        if gaps:
            return f"{b} is the best available option, but note: {', '.join(gaps)}."
        extra = f" ({', '.join(best['pros'][:2])})" if best["pros"] else ""
        return f"{b} is both the nearest and the best match{extra}."
    extra_min = round((best["eta_s"] - nearest["eta_s"]) / 60, 1)
    # "capacity not reported" is a minor tie-breaker; only mention it if nothing else applies
    real = [r for r in nearest["reasons"] if r != "capacity not reported"]
    why = ", ".join(real or nearest["reasons"]) or "a higher overall cost"
    gain = ", ".join(best["pros"][:2])
    return (
        f"{b} chosen over nearest {n} (+{extra_min} min) because {n} is a worse fit: {why}"
        + (f". {b} has {gain}." if gain else ".")
    ).replace("has has ", "has ")
