/**
 * api.js — Centralised API client
 * =================================
 * All HTTP calls to the backend go through this module.
 * No component imports fetch() or a URL string directly — that way,
 * if the backend URL changes, we change it in exactly one place.
 *
 * The BASE_URL uses the Vite proxy: /api in dev is forwarded
 * to http://localhost:8000, so the browser never sees a cross-origin request.
 *
 * WebSocket connections are created directly in the useSimulation hook
 * (not here) because they are stateful and long-lived — they don't fit
 * the request/response model of these helper functions.
 */

const BASE_URL = '/api'

// ── Generic helpers ──────────────────────────────────────────────────────────

async function _get(path) {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) throw new Error(`GET ${path} failed: HTTP ${res.status}`)
  return res.json()
}

async function _post(path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `POST ${path} failed: HTTP ${res.status}`)
  }
  return res.json()
}

async function _delete(path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`DELETE ${path} failed: HTTP ${res.status}`)
  return res.json()
}


// ── Stage 1: Health ──────────────────────────────────────────────────────────

/**
 * Calls GET /health and returns the parsed JSON body.
 * Used by App.jsx on startup to confirm backend connectivity.
 */
export async function checkHealth() {
  return _get('/health/')
}


// ── Stage 2: Routing + Congestion ────────────────────────────────────────────

/** Fetch basic graph statistics (node count, edge count, bbox). */
export async function getGraphInfo() {
  return _get('/route/graph-info')
}

/**
 * Compute the optimal route between two geographic points.
 * @param {number} startLat
 * @param {number} startLng
 * @param {number} endLat
 * @param {number} endLng
 */
export async function computeRoute(startLat, startLng, endLat, endLng) {
  return _post('/route', {
    start_lat: startLat,
    start_lng: startLng,
    end_lat: endLat,
    end_lng: endLng,
  })
}

/** Inject a congestion event near a geographic point. */
export async function injectCongestion(lat, lng, multiplier = 2.0, radiusM = 200) {
  return _post('/congestion/inject', { lat, lng, multiplier, radius_m: radiusM })
}

/** Return all active congestion entries. */
export async function getCongestion() {
  return _get('/congestion')
}

/** Remove all active congestion (reset for a fresh demo run). */
export async function clearCongestion() {
  return _delete('/congestion/clear')
}


// ── Stage 3: Simulation ──────────────────────────────────────────────────────

/**
 * Create a new ambulance simulation.
 *
 * @param {string} ambulanceId       - Unique ambulance identifier
 * @param {number} startLat
 * @param {number} startLng
 * @param {number} endLat
 * @param {number} endLng
 * @param {string} severity          - 'critical' | 'serious' | 'moderate'
 * @param {string} hospitalId        - Destination hospital ID
 * @param {number} speedMultiplier   - Demo speed factor (1–20)
 * @param {number} tickIntervalS     - Seconds between telemetry pings
 * @returns {Promise<{ambulance_id, state, route, hospital, message}>}
 */
export async function startSimulation({
  ambulanceId,
  startLat,
  startLng,
  endLat,
  endLng,
  severity = 'critical',
  hospitalId = 'govt-gen-hosp',
  speedMultiplier = 5.0,
  tickIntervalS = 0.5,
  emergencyType = 'Unknown',
  bloodType = 'Unknown',
  notes = '',
  gcs = null,
  spo2 = null,
  hr = null,
  bpSys = null,
  bpDia = null,
}) {
  return _post('/sim/start', {
    ambulance_id: ambulanceId,
    start_lat: startLat,
    start_lng: startLng,
    end_lat: endLat,
    end_lng: endLng,
    severity,
    hospital_id: hospitalId,
    speed_multiplier: speedMultiplier,
    tick_interval_s: tickIntervalS,
    emergency_type: emergencyType,
    blood_type: bloodType,
    notes,
    gcs,
    spo2,
    hr,
    bp_sys: bpSys,
    bp_dia: bpDia,
  })
}


/**
 * Inject congestion near a point and reroute a running simulation.
 */
export async function rerouteSimulation(ambulanceId, lat, lng, multiplier = 4.0, radiusM = 400) {
  return _post(`/sim/${ambulanceId}/reroute`, { lat, lng, multiplier, radius_m: radiusM })
}

/** Cancel a running simulation. */
export async function cancelSimulation(ambulanceId) {
  return _post(`/sim/${ambulanceId}/cancel`, {})
}

/** List all active simulations. */
export async function listSimulations() {
  return _get('/sim/list')
}


// ── Stage 4: Signal Priority ─────────────────────────────────────────────────

/** Get current state of all signal controllers. */
export async function getSignalStatus() {
  return _get('/signals/status')
}

/** Get aggregate signal priority statistics. */
export async function getSignalStats() {
  return _get('/signals/stats')
}

/** Reset all signal controllers (between demo runs). */
export async function resetSignals() {
  return _post('/signals/reset', {})
}


// ── Stage 5: Hospitals + Alerts ──────────────────────────────────────────────

/** List all hospitals in the registry. */
export async function getHospitals() {
  return _get('/hospitals')
}

/** Get one hospital by ID. */
export async function getHospital(hospitalId) {
  return _get(`/hospitals/${hospitalId}`)
}

/** Find nearest hospital to coordinates. */
export async function findNearestHospital(lat, lng, hospitalType = null) {
  return _post('/hospitals/nearest', { lat, lng, hospital_type: hospitalType })
}

/** List all hospital pre-alerts. */
export async function getAlerts(status = null) {
  const qs = status ? `?status=${status}` : ''
  return _get(`/alerts${qs}`)
}

/** Acknowledge a pre-alert. */
export async function acknowledgeAlert(alertId) {
  return _post(`/alerts/${alertId}/ack`, {})
}

/** Clear all alerts (demo reset). */
export async function resetAlerts() {
  return _post('/alerts/reset', {})
}

// ── Composite reset ──────────────────────────────────────────────────────────

/**
 * Full demo reset — clears signals, alerts, and congestion.
 * Called by the Reset All button in SimControls.
 */
export async function resetAll() {
  await Promise.allSettled([
    resetAlerts(),
    resetSignals(),
    clearCongestion(),
  ])
}
