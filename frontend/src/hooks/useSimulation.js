/**
 * useSimulation.js — Custom hook for the full ambulance simulation lifecycle
 * ===========================================================================
 *
 * This hook owns ALL simulation state.  App.jsx and SimControls.jsx trigger
 * actions; everything else just reads from this hook's return values.
 *
 * Sequence:
 *   1. User clicks Start → start() is called
 *   2. POST /api/sim/start creates the simulation on the backend + returns route
 *   3. WebSocket opens to /sim/{id}/ws
 *   4. Backend pushes 'telemetry' messages every tick — we update state
 *   5. Backend pushes 'alert' messages when ambulance crosses distance thresholds
 *   6. User clicks Reroute → triggerReroute() POSTs to /sim/{id}/reroute
 *   7. Backend sends 'reroute' message with new coords
 *   8. Ambulance arrives → backend sends 'arrived' → WS closes, simState = 'arrived'
 *
 * Why a custom hook rather than Redux/Zustand?
 *   The simulation is a single, coherent piece of state with a clear lifecycle.
 *   A hook co-locates the state with the logic that manages it, and is trivially
 *   testable in isolation.  Adding a global store would be overkill for a demo.
 */

import { useState, useRef, useCallback, useEffect } from 'react'
import { startSimulation, rerouteSimulation } from '../api'

// Fixed Benz Circle (start) and Kanaka Durga Temple (end) as demo defaults.
// These are the same coordinates used throughout backend testing.
export const DEFAULT_START = { lat: 16.5062, lng: 80.6480 }
export const DEFAULT_END   = { lat: 16.5193, lng: 80.6305 }

export function useSimulation() {
  // ── Simulation lifecycle state ───────────────────────────────────────────
  // 'idle' → 'starting' → 'running' → 'arrived' | 'error'
  const [simState, setSimState] = useState('idle')

  // ── Data state ───────────────────────────────────────────────────────────
  /** Last telemetry ping from the backend */
  const [telemetry, setTelemetry] = useState(null)
  /** Signal intersection states from the last telemetry ping */
  const [signals, setSignals]     = useState([])
  /** Accumulated alert events (initial, update, arrival) */
  const [alerts, setAlerts]       = useState([])
  /** Final stats when ambulance arrives */
  const [arrivedData, setArrivedData] = useState(null)
  /** Route data from POST /sim/start response */
  const [route, setRoute]         = useState(null)
  /** Hospital data from POST /sim/start response */
  const [hospital, setHospital]   = useState(null)
  /** True while a reroute POST is in flight */
  const [rerouting, setRerouting] = useState(false)

  // ── Internal refs ─────────────────────────────────────────────────────────
  // We use refs (not state) for values only needed inside async callbacks,
  // to avoid stale closure problems without triggering re-renders.
  const wsRef           = useRef(null)   // The live WebSocket
  const ambulanceIdRef  = useRef('AMB-001')
  const startTimeRef    = useRef(null)   // For elapsed-time calculation in MetricsPanel

  // ── WebSocket URL ─────────────────────────────────────────────────────────
  // We connect directly to the backend WS port (not via Vite proxy) because
  // Vite's HTTP proxy doesn't forward WebSocket upgrades reliably in all
  // configurations.  Using a hardcoded ws:// URL is simpler and equally fine
  // for a local demo.
  const WS_BASE = 'ws://localhost:8000'

  // ── start() ──────────────────────────────────────────────────────────────
  /**
   * Start a new simulation.
   *
   * @param {object} params
   * @param {string} params.ambulanceId
   * @param {string} params.hospitalId
   * @param {string} params.severity        'critical' | 'serious' | 'moderate'
   * @param {number} params.speedMultiplier  1–20
   */
  const start = useCallback(async ({
    ambulanceId   = 'AMB-001',
    hospitalId    = 'govt-gen-hosp',
    endLat        = null,
    endLng        = null,
    severity      = 'critical',
    speedMultiplier = 5,
    emergencyType = 'Unknown',
    bloodType     = 'Unknown',
    notes         = '',
  } = {}) => {
    // Guard: don't start if already running
    if (simState === 'running' || simState === 'starting') return

    // If no destination provided, log a clear error rather than silently routing wrong
    if (endLat == null || endLng == null) {
      console.error('[useSimulation] start() called without endLat/endLng — cannot route to hospital')
      return
    }

    // Reset state from any previous run
    setTelemetry(null)
    setSignals([])
    setAlerts([])
    setArrivedData(null)
    setRerouting(false)
    setSimState('starting')
    ambulanceIdRef.current = ambulanceId

    try {
      const data = await startSimulation({
        ambulanceId,
        startLat: DEFAULT_START.lat,
        startLng: DEFAULT_START.lng,
        endLat,
        endLng,
        severity,
        hospitalId,
        speedMultiplier,
        tickIntervalS: 0.5,
        emergencyType,
        bloodType,
        notes,
      })

      setRoute(data.route)
      setHospital(data.hospital)

      // Step 2: Open WebSocket
      // tick param matches our tickIntervalS so backend and frontend stay in sync
      const ws = new WebSocket(`${WS_BASE}/sim/${ambulanceId}/ws?tick=0.5`)
      wsRef.current = ws
      startTimeRef.current = Date.now()

      ws.onopen = () => {
        setSimState('running')
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          handleWsMessage(msg)
        } catch (err) {
          console.error('[useSimulation] WS parse error:', err)
        }
      }

      ws.onerror = (err) => {
        console.error('[useSimulation] WS error:', err)
        setSimState('error')
      }

      ws.onclose = () => {
        // Only revert to idle if we haven't already marked as arrived
        setSimState((prev) => (prev === 'arrived' ? 'arrived' : 'idle'))
      }

    } catch (err) {
      console.error('[useSimulation] start() failed:', err)
      setSimState('error')
    }
  }, [simState])

  // ── handleWsMessage() ────────────────────────────────────────────────────
  // Defined as a plain function (not useCallback) because it is only called
  // inside ws.onmessage which is set up once per WS connection.  No stale
  // closure risk here.
  function handleWsMessage(msg) {
    switch (msg.type) {
      case 'telemetry':
        setTelemetry(msg.data)
        // Signal states are embedded in the telemetry ping for atomicity.
        // If we sent them as a separate WS message, the frontend might render
        // ambulance position and signal states from different ticks.
        setSignals(msg.data.signals || [])
        break

      case 'alert':
        // Append with timestamp so AlertPanel can show time-of-event
        setAlerts((prev) => [{ ...msg.data, receivedAt: Date.now() }, ...prev])
        break

      case 'reroute':
        // Update route coords so the map polyline redraws the new path
        setRoute((prev) => prev ? { ...prev, coords: msg.data.new_coords } : prev)
        break

      case 'arrived':
        setArrivedData(msg.data)
        setSimState('arrived')
        wsRef.current?.close()
        break

      case 'error':
        console.error('[useSimulation] Backend WS error:', msg.data?.message)
        setSimState('error')
        break

      default:
        // Unknown message types are silently ignored to be forward-compatible
        break
    }
  }

  // ── stop() ───────────────────────────────────────────────────────────────
  /** Cancel the running simulation. */
  const stop = useCallback(() => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Polite cancellation — backend will cleanup the simulation object
      ws.send(JSON.stringify({ type: 'cancel' }))
      ws.close()
    }
    wsRef.current = null
    setSimState('idle')
  }, [])

  // ── triggerReroute() ─────────────────────────────────────────────────────
  /**
   * Inject congestion near the ambulance's current position and ask the
   * backend to recompute the route.
   *
   * We use the ambulance's current lat/lng (from the latest telemetry) as the
   * congestion injection point — this guarantees the congestion is ahead of
   * the ambulance and actually forces a reroute.
   */
  const triggerReroute = useCallback(async () => {
    if (rerouting) return  // prevent double-click
    setRerouting(true)

    // Read telemetry from state via a ref-style approach
    // (we use a functional update below to get current state)
    setTelemetry((currentTelemetry) => {
      if (currentTelemetry) {
        rerouteSimulation(
          ambulanceIdRef.current,
          currentTelemetry.lat,
          currentTelemetry.lng,
          4.0,
          400,
        ).finally(() => setRerouting(false))
      } else {
        setRerouting(false)
      }
      return currentTelemetry  // don't change telemetry state
    })
  }, [rerouting])

  // ── Cleanup on unmount ───────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [])

  // ── Derived values ───────────────────────────────────────────────────────
  const isConnected = simState === 'running'

  return {
    simState,       // 'idle' | 'starting' | 'running' | 'arrived' | 'error'
    telemetry,      // latest TelemetryPing data object, or null
    signals,        // array of signal snapshot objects
    alerts,         // array of alert event objects (newest first)
    arrivedData,    // final mission stats, or null
    route,          // {coords: [[lat,lng],...], ...}
    hospital,       // hospital object from registry, or null
    rerouting,      // true while reroute POST is in flight
    isConnected,    // convenience boolean
    startTimeRef,   // for MetricsPanel elapsed timer
    start,          // fn(params) → void
    stop,           // fn() → void
    triggerReroute, // fn() → void
  }
}
