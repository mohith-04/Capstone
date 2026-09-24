/**
 * AlertPanel.jsx — Hospital pre-alert display
 * ============================================
 *
 * Two sections:
 *   1. Hospital info card — shows capability summary of the destination hospital
 *   2. Alert events list — shows each WS 'alert' message as a colour-coded card
 *
 * Alert events accumulate as the ambulance approaches:
 *   initial  → sent at 2km (blue)
 *   update   → sent at 1km (yellow)
 *   arrival  → sent at 50m (green)
 */

import './AlertPanel.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtEta(seconds) {
  if (seconds == null) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function fmtDist(m) {
  if (m == null) return '—'
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`
}

function fmtTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString()
}

// ── Hospital info ─────────────────────────────────────────────────────────────

function HospitalInfo({ hospital: h }) {
  if (!h) {
    return (
      <div className="hospital-info">
        <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
          No hospital selected
        </p>
      </div>
    )
  }

  return (
    <div className="hospital-info">
      <div className="hospital-name">{h.name}</div>
      <span className={`hospital-type-badge hospital-type-badge--${h.type}`}>
        {h.type.replace('_', ' ')}
      </span>
      <div className="hospital-caps">
        <div className="hospital-cap">Trauma bays <span>{h.trauma_bays}</span></div>
        <div className="hospital-cap">ICU beds <span>{h.icu_beds}</span></div>
        <div className="hospital-cap">
          Blood bank <span className={h.blood_bank ? 'cap-yes' : 'cap-no'}>
            {h.blood_bank ? 'Yes' : 'No'}
          </span>
        </div>
        <div className="hospital-cap">
          Cath lab <span className={h.cath_lab ? 'cap-yes' : 'cap-no'}>
            {h.cath_lab ? 'Yes' : 'No'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Alert card ────────────────────────────────────────────────────────────────

const EVENT_HEADERS = {
  initial: 'PRE-ALERT SENT',
  update:  'ETA UPDATE',
  arrival: 'ARRIVED',
}

function AlertCard({ alertEvent: e }) {
  return (
    <div className={`alert-card alert-card--${e.event}`}>
      <div className={`alert-event-header alert-event-header--${e.event}`}>
        {EVENT_HEADERS[e.event] || e.event.toUpperCase()}
      </div>
      {e.eta_s != null && (
        <div className="alert-row">
          ETA <span>{fmtEta(e.eta_s)}</span>
        </div>
      )}
      {e.distance_m != null && (
        <div className="alert-row">
          Distance <span>{fmtDist(e.distance_m)}</span>
        </div>
      )}
      <div className="alert-time">{fmtTime(e.receivedAt)}</div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

/**
 * @param {{ alerts: Array, hospital: object|null }} props
 */
export default function AlertPanel({ alerts = [], hospital }) {
  return (
    <div className="alert-panel">
      <HospitalInfo hospital={hospital} />

      <div className="alert-events">
        {alerts.length === 0 ? (
          <p className="alert-empty">Awaiting dispatch…</p>
        ) : (
          alerts.map((e, i) => (
            <AlertCard key={`${e.alert_id}-${i}`} alertEvent={e} />
          ))
        )}
      </div>
    </div>
  )
}
