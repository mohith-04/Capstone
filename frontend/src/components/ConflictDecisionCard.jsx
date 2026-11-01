/**
 * ConflictDecisionCard.jsx — Live conflict resolution explanation overlay
 * ========================================================================
 *
 * Appears automatically over the map when two ambulances are competing at
 * the same signal intersection.  Shows WHO won, WHO lost, and most
 * importantly WHY — making the three-tier priority logic self-explanatory
 * to an examiner without any narration.
 *
 * Disappears automatically when the conflict resolves.
 *
 * Props
 * -----
 *   conflict  object|null   The active conflict signal snapshot from
 *                           useDualSimulation().activeConflict.
 *                           Has: conflict_reason, conflict_winner_id,
 *                           conflict_winner_eta, conflict_winner_sev,
 *                           conflict_loser_id, conflict_loser_eta,
 *                           conflict_loser_sev
 */

import './ConflictDecisionCard.css'

const REASON_LABELS = {
  eta:          { icon: '📏', label: 'Lower ETA',       desc: 'Nearest ambulance gets priority first' },
  severity:     { icon: '⚡', label: 'Higher Severity',  desc: 'Equal ETA — case severity is the tiebreaker' },
  distance:     { icon: '📍', label: 'Closer Distance',  desc: 'Equal ETA & severity — shorter distance wins' },
  request_time: { icon: '🕐', label: 'Earlier Request',  desc: 'All else equal — first requester wins' },
}

const SEV_COLORS = {
  critical: '#f85149',
  serious:  '#d29922',
  moderate: '#3fb950',
}

const SEV_ICONS = {
  critical: '🔴',
  serious:  '🟠',
  moderate: '🟡',
}

export default function ConflictDecisionCard({ conflict }) {
  if (!conflict) return null

  const {
    conflict_reason,
    conflict_winner_id,
    conflict_winner_eta,
    conflict_winner_sev,
    conflict_loser_id,
    conflict_loser_eta,
    conflict_loser_sev,
  } = conflict

  const reason = REASON_LABELS[conflict_reason] || { icon: '?', label: conflict_reason, desc: '' }

  function fmtEta(s) {
    if (s == null) return '—'
    return `${Math.round(s)}s`
  }

  return (
    <div className="conflict-card">
      {/* ── Title ────────────────────────────────────────────────────── */}
      <div className="conflict-card__title">
        <span className="conflict-card__pulse" />
        ⚡ CONFLICT RESOLVED
      </div>

      {/* ── Decision reason ──────────────────────────────────────────── */}
      <div className="conflict-card__reason">
        <span className="conflict-card__reason-icon">{reason.icon}</span>
        <div>
          <div className="conflict-card__reason-label">Decided by: {reason.label}</div>
          <div className="conflict-card__reason-desc">{reason.desc}</div>
        </div>
      </div>

      {/* ── Comparison table ─────────────────────────────────────────── */}
      <div className="conflict-card__table">
        {/* Winner row */}
        <div className="conflict-card__row conflict-card__row--winner">
          <div className="conflict-card__badge conflict-card__badge--winner">✓ WINNER</div>
          <div className="conflict-card__id">{conflict_winner_id || '—'}</div>
          <div className="conflict-card__meta">
            <span>ETA: <strong>{fmtEta(conflict_winner_eta)}</strong></span>
            <span>
              {SEV_ICONS[conflict_winner_sev]} <strong>{conflict_winner_sev || '—'}</strong>
            </span>
          </div>
        </div>

        {/* Loser row */}
        <div className="conflict-card__row conflict-card__row--loser">
          <div className="conflict-card__badge conflict-card__badge--loser">⏳ WAITING</div>
          <div className="conflict-card__id">{conflict_loser_id || '—'}</div>
          <div className="conflict-card__meta">
            <span>ETA: <strong>{fmtEta(conflict_loser_eta)}</strong></span>
            <span>
              {SEV_ICONS[conflict_loser_sev]} <strong>{conflict_loser_sev || '—'}</strong>
            </span>
          </div>
        </div>
      </div>

      <div className="conflict-card__footer">
        Loser will be served automatically once winner clears the intersection
      </div>
    </div>
  )
}
