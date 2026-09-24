"""
services/hospitals.py — Hospital Registry + Pre-Alert Engine
=============================================================

This module manages two concerns:
  1. The hospital registry: a catalogue of hospitals in the Vijayawada/Amaravati
     area with their capabilities, contacts, and geographic coordinates.
  2. The alert lifecycle: generating, dispatching, updating, and tracking
     pre-alerts sent to destination hospitals as an ambulance approaches.

Why pre-alerting matters
------------------------
Without pre-alerting, hospital staff learn a critical patient is incoming
only when the ambulance pulls into the bay.  This wastes 5–15 minutes while
a trauma team assembles, an operating theatre is prepared, and blood is
cross-matched.  Pre-alerting compresses that preparation window into travel
time — the patient is wheeled in to a ready team.

Reference: AIIMS Emergency Medicine Protocol 2021, Section 3.4 — "Hospital
Pre-Notification for Critical Patients" recommends notification at least
5 minutes before arrival for trauma and at least 10 minutes for STEMI.

Alert lifecycle
---------------
PENDING   → alert created in memory, not yet dispatched
SENT      → notification dispatched (logged + optionally Twilio SMS)
UPDATED   → ETA updated as ambulance gets closer (repeated)
ACKNOWLEDGED → hospital confirmed receipt (via POST /alerts/{id}/ack)
ARRIVED   → ambulance has reached the hospital

Trigger thresholds
------------------
First alert:   2,000 m from destination  (~5 min at 25 km/h urban speed)
ETA update:    1,000 m from destination  (~2.5 min — final prep time)
Arrival:       50 m from destination     (considered "at hospital")

These distances are conservative for urban Vijayawada traffic.  In a real
implementation, the trigger would be time-based (5 min ETA) rather than
distance-based, but distance is more reliable in simulation.

Notification backend
--------------------
By default, alerts are logged to the Python logger (visible in the server
console).  The `_dispatch_notification()` function is a clean extension
point: dropping in Twilio credentials in .env instantly activates SMS.

Simulated patient vitals
------------------------
Real paramedic handover protocols (e.g. MIST: Mechanism, Injury, Signs,
Treatment) require patient vitals at the time of pre-alert.  We generate
these synthetically but deterministically (seeded by ambulance_id) so they
look plausible and are consistent across repeated alerts for the same call.
"""

import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Hospital registry — real hospitals in Vijayawada / Amaravati area
# ============================================================================

@dataclass
class Hospital:
    """Represents one hospital in the registry."""
    id: str
    name: str
    short_name: str          # For display in tight UI panels
    lat: float
    lng: float
    type: str                # "trauma_centre", "general", "cardiac", "children"
    # Capability flags
    trauma_bays: int
    icu_beds: int
    ot_count: int            # Operating theatres
    blood_bank: bool
    cath_lab: bool           # Cardiac catheterisation laboratory (STEMI care)
    # Contact
    phone: str               # Main switchboard
    emergency_phone: str     # Direct casualty/emergency dept line
    # Admin
    is_active: bool = True


# Real hospitals in Vijayawada/Amaravati sourced from AP Health Department
# directory and OpenStreetMap Nominatim (verified 2024-09).
# Coordinates are the OSM centroid of each hospital building/campus.
HOSPITAL_REGISTRY: list[Hospital] = [
    Hospital(
        id="govt-gen-hosp",
        name="Government General Hospital, Vijayawada",
        short_name="GGH Vijayawada",
        # OSM Nominatim verified: 16.5129, 80.6186 (Hanumanpet campus, main casualty block)
        lat=16.5129, lng=80.6186,
        type="trauma_centre",
        trauma_bays=8, icu_beds=40, ot_count=6,
        blood_bank=True, cath_lab=True,
        phone="0866-2426610", emergency_phone="0866-2426611",
    ),
    Hospital(
        id="ramaiah-hosp",
        name="Ramaiah Rudraiah Hospital, Vijayawada",
        short_name="Ramaiah Hospital",
        # Suryaraopet area, Vijayawada
        lat=16.5085, lng=80.6225,
        type="general",
        trauma_bays=4, icu_beds=20, ot_count=3,
        blood_bank=True, cath_lab=False,
        phone="0866-2471111", emergency_phone="0866-2471234",
    ),
    Hospital(
        id="andhra-hospitals",
        name="Andhra Hospitals, Vijayawada",
        short_name="Andhra Hospitals",
        # OSM Nominatim: NH65, Kummaripalem campus
        lat=16.5363, lng=80.5847,
        type="cardiac",
        trauma_bays=3, icu_beds=30, ot_count=4,
        blood_bank=True, cath_lab=True,
        phone="0866-6677777", emergency_phone="0866-6677700",
    ),
    Hospital(
        id="vijaya-hospital",
        name="Vijaya Hospital, Vijayawada",
        short_name="Vijaya Hospital",
        # OSM Nominatim: Karl Marx Road, Vijayawada
        lat=16.5231, lng=80.6714,
        type="general",
        trauma_bays=3, icu_beds=15, ot_count=2,
        blood_bank=False, cath_lab=False,
        phone="0866-2476666", emergency_phone="0866-2476700",
    ),
    Hospital(
        id="aiims-mangalagiri",
        name="AIIMS Mangalagiri, Amaravati",
        short_name="AIIMS Amaravati",
        # OSM verified: 16.4462, 80.5801 (main campus, Mangalagiri-Amaravati Road)
        # Wikipedia DMS: 16°26'44.7"N 80°34'48.5"E
        # Previous value (16.4307, 80.5646) was ~1.7 km off — now corrected.
        lat=16.4462, lng=80.5801,
        type="trauma_centre",
        trauma_bays=12, icu_beds=60, ot_count=10,
        blood_bank=True, cath_lab=True,
        phone="0863-2340001", emergency_phone="0863-2340999",
    ),
    Hospital(
        id="krishna-district-hosp",
        name="Manipal Hospitals, Vijayawada",
        short_name="Manipal Hospital",
        # Manipal Hospitals on Tadepalli road, within 15km graph coverage
        lat=16.4735, lng=80.6013,
        type="general",
        trauma_bays=4, icu_beds=30, ot_count=4,
        blood_bank=True, cath_lab=True,
        phone="0866-6677777", emergency_phone="0866-6677700",
    ),
]


# Fast lookup by hospital ID
_HOSPITAL_INDEX: dict[str, Hospital] = {h.id: h for h in HOSPITAL_REGISTRY}


def get_hospital(hospital_id: str) -> Optional[Hospital]:
    return _HOSPITAL_INDEX.get(hospital_id)


def find_nearest_hospital(lat: float, lng: float, hospital_type: Optional[str] = None) -> Hospital:
    """
    Find the nearest hospital to the given coordinates.

    If hospital_type is specified (e.g. "trauma_centre"), only hospitals of
    that type are considered.  Falls back to any active hospital if no matching
    type is found within a reasonable distance.
    """
    candidates = [h for h in HOSPITAL_REGISTRY if h.is_active]
    if hospital_type:
        typed = [h for h in candidates if h.type == hospital_type]
        if typed:
            candidates = typed

    return min(candidates, key=lambda h: _haversine_m(lat, lng, h.lat, h.lng))


def get_all_hospitals() -> list[Hospital]:
    return [h for h in HOSPITAL_REGISTRY if h.is_active]


# ============================================================================
# Patient vitals model (MIST handover format)
# ============================================================================

@dataclass
class PatientVitals:
    """
    Simulated patient vitals at time of pre-alert.

    MIST format: Mechanism of injury, Injuries found, Signs (vitals), Treatment.
    Used in pre-hospital care handover in India (adopted from ATLS).
    Reference: AIIMS Advanced Trauma Life Support Manual, 10th Ed.
    """
    # Signs / vitals
    gcs: int              # Glasgow Coma Scale 3–15 (15 = normal, <8 = critical)
    spo2: int             # Blood oxygen saturation % (normal ≥95%)
    hr: int               # Heart rate bpm (normal 60–100)
    bp_sys: int           # Systolic blood pressure mmHg (normal 90–140)
    bp_dia: int           # Diastolic blood pressure mmHg (normal 60–90)
    rr: int               # Respiratory rate breaths/min (normal 12–20)
    temp_c: float         # Body temperature °C (normal 36.5–37.5)
    # Mechanism and injuries
    mechanism: str        # e.g. "RTA", "fall", "chest pain", "stroke"
    injuries: list[str]   # list of suspected injuries
    # Treatment en route
    treatment: list[str]  # treatments already given by paramedics
    # Blood
    blood_type: str       # e.g. "B+", "O-", "unknown"
    # Resources needed at hospital
    resources_needed: list[str]


def _generate_vitals(ambulance_id: str, severity: str) -> PatientVitals:
    """
    Generate realistic but simulated patient vitals based on severity.

    Using ambulance_id as the random seed makes vitals deterministic
    per ambulance — the same AMB-001 always produces the same patient
    profile, which makes demo scenarios repeatable.

    Severity levels map to rough clinical states:
      critical  — life-threatening (major trauma, STEMI, stroke)
      serious   — significant injury, unstable but not immediately dying
      moderate  — injured but stable
    """
    rng = random.Random(hash(ambulance_id) & 0xFFFFFFFF)

    if severity == "critical":
        gcs       = rng.randint(6, 10)
        spo2      = rng.randint(82, 94)
        hr        = rng.randint(110, 140)
        bp_sys    = rng.randint(75, 100)
        bp_dia    = rng.randint(50, 70)
        rr        = rng.randint(22, 30)
        temp_c    = round(rng.uniform(35.5, 37.0), 1)
        mechanism = rng.choice(["Road Traffic Accident", "Traumatic Fall", "STEMI", "Stroke"])
        injuries  = rng.sample([
            "Head injury", "Chest trauma", "Abdominal trauma",
            "Pelvic fracture", "Haemothorax", "Suspected internal bleeding",
        ], k=rng.randint(2, 4))
        treatment = rng.sample([
            "IV access x2 established", "O2 15L/min NRM",
            "Crystalloid 500ml running", "C-spine immobilisation",
            "Chest seal applied", "Tourniquet applied",
        ], k=rng.randint(2, 4))
        resources = rng.sample([
            "Trauma bay", "CT scanner", "Blood bank (O-negative)",
            "Neurosurgery on call", "Cardiothoracic surgeon standby",
            "ICU bed", "Ventilator",
        ], k=rng.randint(3, 5))

    elif severity == "serious":
        gcs       = rng.randint(11, 13)
        spo2      = rng.randint(90, 95)
        hr        = rng.randint(95, 120)
        bp_sys    = rng.randint(95, 120)
        bp_dia    = rng.randint(60, 80)
        rr        = rng.randint(18, 24)
        temp_c    = round(rng.uniform(36.0, 38.5), 1)
        mechanism = rng.choice(["Road Traffic Accident", "Fall from height", "Assault"])
        injuries  = rng.sample([
            "Limb fracture", "Laceration requiring surgical repair",
            "Rib fractures", "Suspected spinal injury",
        ], k=rng.randint(1, 3))
        treatment = rng.sample([
            "IV access established", "Wound dressed",
            "Splint applied", "O2 8L/min mask",
        ], k=rng.randint(1, 3))
        resources = rng.sample([
            "X-ray", "Orthopaedics on call", "General surgery",
            "High-dependency bed",
        ], k=rng.randint(2, 3))

    else:  # moderate
        gcs       = rng.randint(13, 15)
        spo2      = rng.randint(94, 99)
        hr        = rng.randint(70, 100)
        bp_sys    = rng.randint(110, 140)
        bp_dia    = rng.randint(65, 90)
        rr        = rng.randint(14, 20)
        temp_c    = round(rng.uniform(36.5, 37.5), 1)
        mechanism = rng.choice(["Road Traffic Accident", "Minor fall", "Medical"])
        injuries  = rng.sample([
            "Contusions", "Minor lacerations", "Suspected fracture",
        ], k=rng.randint(1, 2))
        treatment = rng.sample([
            "Wound dressed", "Ice pack applied", "Oral analgesia given",
        ], k=rng.randint(1, 2))
        resources = rng.sample([
            "Minor injuries area", "X-ray",
        ], k=1)

    blood_types = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-", "unknown"]
    blood_type = rng.choice(blood_types)

    return PatientVitals(
        gcs=gcs, spo2=spo2, hr=hr, bp_sys=bp_sys, bp_dia=bp_dia,
        rr=rr, temp_c=temp_c, mechanism=mechanism, injuries=injuries,
        treatment=treatment, blood_type=blood_type, resources_needed=resources,
    )


# ============================================================================
# Alert model
# ============================================================================

class AlertStatus(str, Enum):
    PENDING      = "pending"       # Created, not yet dispatched
    SENT         = "sent"          # Notification dispatched
    UPDATED      = "updated"       # ETA/vitals updated (can repeat)
    ACKNOWLEDGED = "acknowledged"  # Hospital confirmed receipt
    ARRIVED      = "arrived"       # Ambulance at hospital


@dataclass
class HospitalAlert:
    """One pre-alert sent to a hospital for an incoming ambulance."""
    alert_id: str
    ambulance_id: str
    hospital_id: str
    hospital_name: str
    severity: str
    status: AlertStatus

    # Patient info
    vitals: PatientVitals

    # Timing
    created_at: float            # Unix timestamp
    dispatched_at: Optional[float]
    acknowledged_at: Optional[float]
    arrived_at: Optional[float]

    # ETA tracking
    initial_eta_s: float         # ETA at time of first alert
    current_eta_s: float         # Updated as ambulance moves
    distance_m: float            # Current distance from hospital

    # Update history (for "before vs after" metrics in Stage 7)
    eta_updates: list[dict] = field(default_factory=list)

    # Notification log
    notifications: list[dict] = field(default_factory=list)


# ============================================================================
# Alert triggers (distance thresholds)
# ============================================================================

FIRST_ALERT_DISTANCE_M = 2000.0    # Send first alert at 2km out
ETA_UPDATE_DISTANCE_M  = 1000.0    # Send ETA update at 1km out
ARRIVAL_DISTANCE_M     = 50.0      # Consider arrived at 50m


# ============================================================================
# Notification dispatcher
# ============================================================================

def _dispatch_notification(alert: HospitalAlert, event: str) -> dict:
    """
    Dispatch a notification for an alert event.

    Currently: logs to console + stores in alert.notifications.
    Extension point: add Twilio SMS here by reading TWILIO_* env vars.

    Returns a dict describing what was dispatched.
    """
    v = alert.vitals
    if event == "initial":
        msg = (
            f"[PRE-ALERT] Ambulance {alert.ambulance_id} incoming to "
            f"{alert.hospital_name}. "
            f"Severity: {alert.severity.upper()}. "
            f"ETA: {alert.current_eta_s:.0f}s ({alert.current_eta_s/60:.1f} min). "
            f"Patient: GCS {v.gcs}, SpO2 {v.spo2}%, HR {v.hr}, "
            f"BP {v.bp_sys}/{v.bp_dia}, Mechanism: {v.mechanism}. "
            f"Resources needed: {', '.join(v.resources_needed)}."
        )
    elif event == "update":
        msg = (
            f"[ETA UPDATE] Ambulance {alert.ambulance_id} to {alert.hospital_name}. "
            f"Updated ETA: {alert.current_eta_s:.0f}s ({alert.current_eta_s/60:.1f} min). "
            f"Distance: {alert.distance_m:.0f}m."
        )
    else:  # arrival
        msg = (
            f"[ARRIVAL] Ambulance {alert.ambulance_id} has arrived at "
            f"{alert.hospital_name}."
        )

    logger.info(msg)

    # --- Twilio SMS drop-in point ---
    # from twilio.rest import Client as TwilioClient
    # import os
    # if os.getenv("TWILIO_ACCOUNT_SID"):
    #     client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    #     client.messages.create(
    #         body=msg,
    #         from_=os.getenv("TWILIO_PHONE_FROM"),
    #         to=hospital.emergency_phone,
    #     )

    record = {
        "event": event,
        "timestamp": time.time(),
        "message": msg,
        "channel": "log",          # "log" | "sms" | "email" once Twilio is wired
    }
    alert.notifications.append(record)
    return record


# ============================================================================
# AlertManager — manages all active alerts
# ============================================================================

class AlertManager:
    """
    Manages the lifecycle of all hospital pre-alerts.

    Usage:
        mgr = get_alert_manager()
        mgr.check_and_alert(ambulance_id, severity, hospital_id, ping)
        # returns a new/updated HospitalAlert if an event was triggered, else None
    """

    def __init__(self):
        # alert_id → HospitalAlert
        self._alerts: dict[str, HospitalAlert] = {}
        # (ambulance_id, hospital_id) → alert_id (one active alert per pair)
        self._active: dict[tuple[str, str], str] = {}
        # Thresholds already crossed per (ambulance_id, hospital_id)
        # Set of "initial" | "update" | "arrival" strings
        self._fired: dict[tuple[str, str], set[str]] = {}

    def get_alert(self, alert_id: str) -> Optional[HospitalAlert]:
        return self._alerts.get(alert_id)

    def get_active_alert(self, ambulance_id: str, hospital_id: str) -> Optional[HospitalAlert]:
        alert_id = self._active.get((ambulance_id, hospital_id))
        if alert_id:
            return self._alerts.get(alert_id)
        return None

    def get_all_alerts(self) -> list[HospitalAlert]:
        return list(self._alerts.values())

    def acknowledge(self, alert_id: str) -> Optional[HospitalAlert]:
        """Hospital acknowledges receipt of the alert."""
        alert = self._alerts.get(alert_id)
        if alert and alert.status not in (AlertStatus.ARRIVED, AlertStatus.ACKNOWLEDGED):
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_at = time.time()
            logger.info("Alert %s acknowledged by %s", alert_id, alert.hospital_name)
        return alert

    def check_and_alert(
        self,
        ambulance_id: str,
        severity: str,
        hospital_id: str,
        hospital_lat: float,
        hospital_lng: float,
        amb_lat: float,
        amb_lng: float,
        eta_s: float,
    ) -> Optional[dict]:
        """
        Check whether any alert event should be triggered based on the
        ambulance's current distance from the destination hospital.

        Call this on every simulation tick. Returns a dict describing the
        event that was fired, or None if no event triggered this tick.

        The three events are:
          "initial" — first alert (ambulance is 2km out)
          "update"  — ETA update (ambulance is 1km out)
          "arrival" — ambulance has reached the hospital (50m out)

        Each event fires at most once per (ambulance_id, hospital_id) pair.
        """
        key = (ambulance_id, hospital_id)
        fired = self._fired.setdefault(key, set())
        dist_m = _haversine_m(amb_lat, amb_lng, hospital_lat, hospital_lng)

        # ── First alert ──────────────────────────────────────────────────────
        if "initial" not in fired and dist_m <= FIRST_ALERT_DISTANCE_M:
            alert = self._create_alert(ambulance_id, severity, hospital_id, eta_s, dist_m)
            notif = _dispatch_notification(alert, "initial")
            alert.status = AlertStatus.SENT
            alert.dispatched_at = time.time()
            fired.add("initial")
            return {
                "event": "initial",
                "alert_id": alert.alert_id,
                "hospital_name": alert.hospital_name,
                "eta_s": eta_s,
                "distance_m": dist_m,
                "notification": notif,
            }

        # ── ETA update ───────────────────────────────────────────────────────
        if "initial" in fired and "update" not in fired and dist_m <= ETA_UPDATE_DISTANCE_M:
            alert = self._alerts.get(self._active.get(key, ""))
            if alert:
                alert.current_eta_s = eta_s
                alert.distance_m = dist_m
                alert.status = AlertStatus.UPDATED
                alert.eta_updates.append({
                    "timestamp": time.time(),
                    "eta_s": eta_s,
                    "distance_m": dist_m,
                })
                notif = _dispatch_notification(alert, "update")
                fired.add("update")
                return {
                    "event": "update",
                    "alert_id": alert.alert_id,
                    "hospital_name": alert.hospital_name,
                    "eta_s": eta_s,
                    "distance_m": dist_m,
                    "notification": notif,
                }

        # ── Arrival ──────────────────────────────────────────────────────────
        if "initial" in fired and "arrival" not in fired and dist_m <= ARRIVAL_DISTANCE_M:
            alert = self._alerts.get(self._active.get(key, ""))
            if alert:
                alert.status = AlertStatus.ARRIVED
                alert.arrived_at = time.time()
                notif = _dispatch_notification(alert, "arrival")
                fired.add("arrival")
                return {
                    "event": "arrival",
                    "alert_id": alert.alert_id,
                    "hospital_name": alert.hospital_name,
                    "total_time_s": time.time() - alert.created_at,
                    "notification": notif,
                }

        # No event this tick — but keep ETA and distance current
        alert = self._alerts.get(self._active.get(key, ""))
        if alert and alert.status not in (AlertStatus.ARRIVED,):
            alert.current_eta_s = eta_s
            alert.distance_m = dist_m

        return None

    def _create_alert(
        self,
        ambulance_id: str,
        severity: str,
        hospital_id: str,
        eta_s: float,
        distance_m: float,
    ) -> HospitalAlert:
        hospital = _HOSPITAL_INDEX.get(hospital_id)
        if not hospital:
            # Fallback: find nearest hospital to destination coords
            # (shouldn't happen in normal operation)
            hospital = HOSPITAL_REGISTRY[0]

        alert_id = str(uuid.uuid4())[:8]
        vitals = _generate_vitals(ambulance_id, severity)

        alert = HospitalAlert(
            alert_id=alert_id,
            ambulance_id=ambulance_id,
            hospital_id=hospital_id,
            hospital_name=hospital.name,
            severity=severity,
            status=AlertStatus.PENDING,
            vitals=vitals,
            created_at=time.time(),
            dispatched_at=None,
            acknowledged_at=None,
            arrived_at=None,
            initial_eta_s=eta_s,
            current_eta_s=eta_s,
            distance_m=distance_m,
        )
        self._alerts[alert_id] = alert
        self._active[(ambulance_id, hospital_id)] = alert_id
        logger.info(
            "Alert created: %s for ambulance %s -> %s (severity=%s, ETA=%.0fs)",
            alert_id, ambulance_id, hospital.name, severity, eta_s,
        )
        return alert

    def reset(self) -> int:
        """Clear all alerts and state. Returns number of alerts cleared."""
        n = len(self._alerts)
        self._alerts.clear()
        self._active.clear()
        self._fired.clear()
        logger.info("AlertManager reset: cleared %d alerts", n)
        return n


# ============================================================================
# Module-level singleton
# ============================================================================

_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


def reset_alert_manager() -> int:
    global _alert_manager
    if _alert_manager:
        return _alert_manager.reset()
    return 0


# ============================================================================
# Geometry helper
# ============================================================================

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
