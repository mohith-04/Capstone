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
 *   - ⚠ CONFLICT badge when two ambulances are competing for the same signal
 *   - Pending queue (list of waiting ambulance IDs behind the active one)
 *
 * The mode badges have CSS animations that make the state visually obvious:
 *   priority_pending → yellow pulse    (preemption being set up)
 *   priority_active  → green glow      (GREEN held for ambulance)
 *   restoring        → blue fade       (returning to normal)
 *   normal           → static grey
 *   conflict         → red throb       (two ambulances competing)
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

/** Shorten ambulance ID for display (e.g. "AMB-001" → "AMB-001") */
function shortAmbId(id) {
  return id ? String(id).slice(0, 8) : '—'
}

// ── Sub-components ───────────────────────────────────────────────────────────

function ModeBadge({ mode }) {
  return (
    <span className={`signal-mode signal-mode--${mode}`}>
      {MODE_LABELS[mode] || mode}
    </span>
  )
}

function ConflictBadge() {
  return (
    <span className="signal-conflict-badge" title="Two ambulances are competing for this signal">
      ⚠ CONFLICT
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

function PendingQueue({ queue }) {
  if (!queue || queue.length === 0) return null
  return (
    <div className="signal-pending-queue">
      <span className="signal-pending-label">Queued:</span>
      {queue.map((id) => (
        <span key={id} className="signal-pending-id">{shortAmbId(id)}</span>
      ))}
    </div>
  )
}

function SignalCard({ signal: s }) {
  const hasConflict = s.conflict_detected && s.pending_queue?.length > 0

  return (
    <div className={`signal-card ${hasConflict ? 'signal-card--conflict' : ''}`}>
      {/* Header: node ID + mode badge + optional conflict badge */}
      <div className="signal-card__header">
        <span className="signal-card__id">#{shortNodeId(s.node_id)}</span>
        <div className="signal-card__badges">
          {hasConflict && <ConflictBadge />}
          <ModeBadge mode={s.mode} />
        </div>
      </div>

      {/* Active ambulance info */}
      {s.priority_ambulance_id && (
        <div className="signal-active-amb">
          <span className="signal-active-label">Serving:</span>
          <span className="signal-active-id">{shortAmbId(s.priority_ambulance_id)}</span>
        </div>
      )}

      {/* Pending queue (other ambulances waiting) */}
      <PendingQueue queue={s.pending_queue} />

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

// ── Conflict summary bar ──────────────────────────────────────────────────────

function ConflictSummary({ signals }) {
  const conflicts = signals.filter((s) => s.conflict_detected && s.pending_queue?.length > 0)
  if (conflicts.length === 0) return null
  return (
    <div className="signal-conflict-summary">
      <span className="signal-conflict-summary__icon">⚠</span>
      <span>
        {conflicts.length} intersection{conflicts.length > 1 ? 's' : ''} with competing ambulances
        — priority queue arbitrating automatically
      </span>
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
      <ConflictSummary signals={signals} />
      {signals.map((s) => (
        <SignalCard key={s.node_id} signal={s} />
      ))}
    </div>
  )
}
