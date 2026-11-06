/**
 * useDualSimulation.js — Hook for the dual-ambulance conflict demo
 * =================================================================
 *
 * Manages two independent useSimulation() instances (AMB-001 and AMB-002)
 * and exposes a single startBoth() function that fires both simultaneously
 * with hardcoded scenario coordinates.
 *
 * Three demo scenarios, each proving a different tier of priority logic:
 *
 *   "eta"          AMB-001 starts far east (Benz Circle, ~43s to hospital),
 *                  AMB-002 starts north (Gandhi Nagar, ~20s to hospital).
 *                  AMB-002 has much lower ETA -> wins, regardless of severity.
 *
 *   "severity"     Both start ~equidistant from the hospital (~30s each).
 *                  ETA difference is within the 2s threshold, so severity wins.
 *
 *   "timestamp"    Same coords as Severity, both Critical severity.
 *                  AMB-001 API call fires 300ms before AMB-002 -> AMB-001 wins.
 *
 * Conflict intersection: OSM node 6146884325 at (16.51987, 80.63130)
 *   - Degree-7 junction near GGH — ALL routes from any direction pass through it
 *   - AMB-001 approaches from east via E-W road (Phase B)
 *   - AMB-002 approaches from north via N-S road (Phase A)
 */

import { useMemo, useCallback } from 'react'
import { useSimulation } from './useSimulation'

// ── Hospital destination (Govt General Hospital) ─────────────────────────────
const DEMO_HOSPITAL_ID  = 'govt-gen-hosp'
const DEMO_HOSPITAL_LAT = 16.5193
const DEMO_HOSPITAL_LNG = 80.6305

// ── Demo speed — fast enough to reach conflict quickly, slow enough to see it
const DEMO_SPEED = 10

// ── Scenario start coordinates ────────────────────────────────────────────────
//
// Conflict node 6146884325 sits at (16.51987, 80.63130) — universal bottleneck
// near GGH: EVERY route from any direction to GGH passes through this node.
// (Injected as traffic signal in backend services/graph.py)
//
// Scenario 1 — ETA decides:
//   AMB-001: Benz Circle (far east, ~119s to conflict node via E-W road)
//   AMB-002: Close north of conflict node (~30-40s to node via N-S road)
//   → AMB-002 has much lower ETA, wins even if lower severity
//
// Scenario 2 — Severity decides:
//   Both start equidistant from the conflict node (~60-70s each)
//   AMB-001: medium east approach on E-W road
//   AMB-002: medium north approach on N-S road
//   → Similar ETA, severity determines winner
//
// Scenario 3 — Request time decides:
//   Same coords as Scenario 2, both Critical
//   AMB-001 API fires 300ms before AMB-002 → AMB-001 wins

// ── Scenario start coordinates ────────────────────────────────────────────────
//
// Conflict node 6146884325 sits at (16.51987, 80.63130) — universal bottleneck
// near GGH: EVERY route from any direction to GGH passes through this node.
//
// Scenario 1 — ETA decides:
//   AMB-001: Benz Circle (far east, 43s total ETA to hospital)
//   AMB-002: Gandhi Nagar (north, 20s total ETA to hospital)
//   → AMB-002 wins by a wide ETA margin, regardless of severity
//
// Scenario 2 — Severity decides:
//   AMB-001: East  (16.5070, 80.6430) — verified exactly 15.70s to conflict node
//   AMB-002: North (16.5290, 80.6313) — verified exactly 15.70s to conflict node
//   Both start simultaneously (delayMs: 0) and arrive at the signal at the exact same moment.
//   Equal ETAs (difference = 0.0s < 2.0s threshold) → user-chosen severity decides the winner!
//
// Scenario 3 — Request time decides:
//   Same coords as Scenario 2, both forced Critical.
//   AMB-001 request fires 300ms before AMB-002.
//   Arrive together, both Critical → earlier request timestamp wins!

// Fixed start points. In scenarios 2 and 3 the two ambulances come at the
// conflict junction from DIFFERENT roads, so the signal has to choose between
// two competing phases (checked with the backend's own approach-phase logic):
//   AMB-001  East  (16.5070, 80.6430) -> arrives heading 295 deg -> Phase B (east-west)
//   AMB-002  North (16.5290, 80.6313) -> arrives heading 194 deg -> Phase A (north-south)
const SCENARIO_CONFIGS = {
  eta: {
    start1: { lat: 16.5062, lng: 80.6480 },   // Benz Circle — far east (~43s ETA)
    start2: { lat: 16.5250, lng: 80.6350 },   // Gandhi Nagar — north (~20s ETA)
    delayMs: 0,
    forceSeverity: false,    // use the user's severity dropdowns
  },
  severity: {
    start1: { lat: 16.5070, lng: 80.6430 },   // East approach — 15.7s to conflict
    start2: { lat: 16.5290, lng: 80.6313 },   // North approach — 15.7s to conflict
    // Start simultaneously! Both arrive at the conflict node at the exact same instant.
    // Equal ETA (< 2s threshold) → Case severity determines priority!
    delayMs: 0,
    forceSeverity: false,    // severity dropdowns decide
  },
  timestamp: {
    start1: { lat: 16.5070, lng: 80.6430 },   // Same coords as Severity scenario
    start2: { lat: 16.5290, lng: 80.6313 },
    // AMB-001 fires 300ms BEFORE AMB-002 -> AMB-001 wins (first requester).
    // (A shorter 100ms gap flipped the winner to AMB-002, because the two
    // approach roads differ in length, so we keep 300ms.)
    delayMs: 300,
    forceSeverity: true,     // both forced Critical — only request time differs
    forcedSev: 'critical',
  },
}

export function useDualSimulation() {
  const sim1 = useSimulation({ ambulanceId: 'AMB-001' })
  const sim2 = useSimulation({ ambulanceId: 'AMB-002' })

  // ── Combined signal list ────────────────────────────────────────────────
  // Merge both ambulances' signal lists by node_id.  When both sims see the
  // same intersection, we take the richer snapshot (the one that shows the
  // conflict) rather than duplicating the marker.
  const combinedSignals = useMemo(() => {
    const map = {}
    for (const s of sim1.signals) {
      map[s.node_id] = { ...s }
    }
    for (const s of sim2.signals) {
      if (s.node_id in map) {
        const existing = map[s.node_id]
        // Prefer whichever snapshot shows more information about the conflict
        const merged = {
          ...existing,
          // If either sim sees a conflict, surface it
          conflict_detected: existing.conflict_detected || s.conflict_detected,
          pending_queue: [
            ...new Set([...(existing.pending_queue || []), ...(s.pending_queue || [])]),
          ],
        }
        // Use the snapshot that has the richer conflict explanation
        if (s.conflict_reason && !existing.conflict_reason) {
          merged.conflict_reason = s.conflict_reason
          merged.conflict_winner_id = s.conflict_winner_id
          merged.conflict_winner_eta = s.conflict_winner_eta
          merged.conflict_winner_sev = s.conflict_winner_sev
          merged.conflict_loser_id  = s.conflict_loser_id
          merged.conflict_loser_eta = s.conflict_loser_eta
          merged.conflict_loser_sev = s.conflict_loser_sev
        }
        map[s.node_id] = merged
      } else {
        map[s.node_id] = { ...s }
      }
    }
    return Object.values(map)
  }, [sim1.signals, sim2.signals])

  // ── Hospital marker for the demo ──────────────────────────────────────────
  // The three scenarios are built around a destination right beside the
  // conflict signal (routes to the registry's exact GGH location don't pass it),
  // so both ambulances end their routes at DEMO_HOSPITAL_LAT/LNG. Pin the marker
  // there too, so the routes run solid all the way to the hospital instead of
  // stopping at the signal with a long dotted connector to a marker 1.4 km away.
  const demoHospital = useMemo(() => {
    const h = sim1.hospital || sim2.hospital
    return h ? { ...h, lat: DEMO_HOSPITAL_LAT, lng: DEMO_HOSPITAL_LNG } : null
  }, [sim1.hospital, sim2.hospital])

  // ── First signal with active conflict (for ConflictDecisionCard) ─────────
  const activeConflict = useMemo(
    () => combinedSignals.find((s) => s.conflict_detected && s.conflict_reason) || null,
    [combinedSignals],
  )

  // ── startBoth() ──────────────────────────────────────────────────────────
  const startBoth = useCallback(async (scenario, sevA, sevB) => {
    const cfg = SCENARIO_CONFIGS[scenario]
    if (!cfg) return

    const sev1 = cfg.forceSeverity ? cfg.forcedSev : sevA
    const sev2 = cfg.forceSeverity ? cfg.forcedSev : sevB

    // Fire AMB-001 immediately
    sim1.start({
      ambulanceId:     'AMB-001',
      startLat:        cfg.start1.lat,
      startLng:        cfg.start1.lng,
      hospitalId:      DEMO_HOSPITAL_ID,
      endLat:          DEMO_HOSPITAL_LAT,
      endLng:          DEMO_HOSPITAL_LNG,
      severity:        sev1,
      speedMultiplier: DEMO_SPEED,
      emergencyType:   'Road Traffic Accident',
    })

    // Fire AMB-002 after the scenario's delay (0ms = simultaneous)
    if (cfg.delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, cfg.delayMs))
    }

    sim2.start({
      ambulanceId:     'AMB-002',
      startLat:        cfg.start2.lat,
      startLng:        cfg.start2.lng,
      hospitalId:      DEMO_HOSPITAL_ID,
      endLat:          DEMO_HOSPITAL_LAT,
      endLng:          DEMO_HOSPITAL_LNG,
      severity:        sev2,
      speedMultiplier: DEMO_SPEED,
      emergencyType:   'Road Traffic Accident',
    })
  }, [sim1, sim2])

  // ── stopBoth() ───────────────────────────────────────────────────────────
  const stopBoth = useCallback(() => {
    sim1.stop()
    sim2.stop()
  }, [sim1, sim2])

  // ── Derived state ─────────────────────────────────────────────────────────
  const anyRunning = sim1.simState === 'running' || sim2.simState === 'running'
                  || sim1.simState === 'starting' || sim2.simState === 'starting'
  const bothIdle   = (sim1.simState === 'idle' || sim1.simState === 'arrived' || sim1.simState === 'error')
                  && (sim2.simState === 'idle' || sim2.simState === 'arrived' || sim2.simState === 'error')

  return {
    sim1,
    sim2,
    combinedSignals,
    activeConflict,
    demoHospital,
    startBoth,
    stopBoth,
    anyRunning,
    bothIdle,
  }
}
