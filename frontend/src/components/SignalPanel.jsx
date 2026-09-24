/**
 * SignalPanel.jsx — Traffic signal priority state display
 * ========================================================
 *
 * Renders one card per signal intersection on the route.
 * Each card shows:
 *   - The FSM mode (normal / priority_pending / priority_active / restoring)
 *   - A two-circle traffic light for ambulance direction and cross direction
 *   - ETA (how far away the ambulance is, at current speed)
 *   - Time saved by preemption so far
 *
 * The mode badges have CSS animations that make the state visually obvious:
 *   priority_pending → yellow pulse    (preemption being set up)
 *   priority_active  → green glow      (GREEN held for ambulance)
 *   restoring        → blue fade       (returning to normal)
 *   normal           → static grey
 */

import './SignalPanel.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

const MODE_LABELS = {
  normal:           'Normal',
  priority_pending: 'Pending',
  priority_active:  'PRIORITY',
  restoring:        'Restoring',
}

/** Shorten an OSM node ID to its last 6 digits for compact display */
function shortNodeId(nodeId) {
  const s = String(nodeId)
  return s.length > 6 ? `…${s.slice(-6)}` : s
}

// ── Sub-components ───────────────────────────────────────────────────────────

function ModeBadge({ mode }) {
  return (
    <span className={`signal-mode signal-mode--${mode}`}>
      {MODE_LABELS[mode] || mode}
    </span>
  )
}

function TrafficLight({ colour, label }) {
  return (
    <div className="signal-light-group">
      <div className={`signal-light signal-light--${colour}`} />
      <span className="signal-light-label">{label}</span>
    </div>
  )
}

function SignalCard({ signal: s }) {
  return (
    <div className="signal-card">
      {/* Header: node ID + mode badge */}
      <div className="signal-card__header">
        <span className="signal-card__id">#{shortNodeId(s.node_id)}</span>
        <ModeBadge mode={s.mode} />
      </div>

      {/* Traffic lights: ambulance direction + cross direction */}
      <div className="signal-lights">
        <TrafficLight colour={s.ambulance_direction} label="Ambulance" />
        <TrafficLight colour={s.cross_direction}    label="Cross" />
      </div>

      {/* ETA + time saved */}
      <div className="signal-stats">
        {s.priority_eta_s != null && (
          <span className="signal-eta">
            ETA: {s.priority_eta_s.toFixed(1)}s
          </span>
        )}
        {s.time_saved_s > 0 && (
          <span className="signal-saved">
            Saved: {s.time_saved_s.toFixed(1)}s
          </span>
        )}
      </div>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

/**
 * @param {{ signals: Array }} props
 */
export default function SignalPanel({ signals = [] }) {
  if (signals.length === 0) {
    return (
      <div className="signal-panel">
        <p className="signal-empty">No signal intersections on route</p>
      </div>
    )
  }

  return (
    <div className="signal-panel">
      {signals.map((s) => (
        <SignalCard key={s.node_id} signal={s} />
      ))}
    </div>
  )
}
