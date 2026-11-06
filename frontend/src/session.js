/**
 * session.js — login state + authenticated API calls for accounts/orgs.
 * The JWT lives in localStorage so a refresh keeps you signed in.
 */

const KEY = 'ambulance-session'

export function loadSession() {
  try { return JSON.parse(localStorage.getItem(KEY)) } catch { return null }
}
export function saveSession(s) {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ }
}
export function clearSession() {
  try { localStorage.removeItem(KEY) } catch { /* ignore */ }
}

async function call(method, path, body, token) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const e = new Error(
      typeof data.detail === 'string' ? data.detail
        : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ')
        : `Request failed (HTTP ${res.status})`,
    )
    e.status = res.status
    throw e
  }
  return data
}

export const login            = (b) => call('POST', '/auth/login', b)
export const registerHospital = (b) => call('POST', '/auth/register-hospital', b)
export const registerAmbulance = (b) => call('POST', '/auth/register-ambulance', b)
export const getMe            = (t) => call('GET', '/auth/me', null, t)
export const getFleet         = (t) => call('GET', '/org/fleet', null, t)
export const removeMember     = (t, id) => call('DELETE', `/org/members/${id}`, null, t)
export const rotateCode       = (t) => call('POST', '/org/rotate-code', null, t)
export const getTrips         = (t) => call('GET', '/org/trips', null, t)
export const getCapacity      = (t) => call('GET', '/org/capacity', null, t)
export const putCapacity      = (t, b) => call('PUT', '/org/capacity', b, t)

/** WebSocket URL for the hospital live feed (goes through the Vite /api proxy). */
export function orgSocketUrl(token) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/org/ws?token=${encodeURIComponent(token)}`
}
