/**
 * TelemetryPanel.jsx — Live ambulance telemetry sidebar panel
 * ============================================================
 *
 * Displays the current GPS-like data being streamed from the backend
 * simulation engine.  Every field mirrors what a real vehicle tracker
 * (e.g. Teltonika FMB920) would produce — we're just reading synthetic
 * data instead of a real GPRS feed.
 */

import './TelemetryPanel.css'

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Format seconds as mm:ss (e.g. 723 → '12:03') */
function fmtEta(seconds) {
  if (seconds == null || !isFinite(seconds)) return '--:--'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Format metres: show km with 2 decimal places if ≥ 1000 m */
function fmtDist(metres) {
  if (metres == null) return '—'
  if (metres >= 1000) return `${(metres / 1000).toFixed(2)} km`
  return `${Math.round(metres)} m`
}

/** Return CSS class for congestion level */
function congestionClass(factor) {
  if (factor < 1.5) return 'congestion--clear'
  if (factor < 3.0) return 'congestion--moderate'
  return 'congestion--heavy'
}

/** Return human label for congestion level */
function congestionLabel(factor) {
  if (factor < 1.5) return 'Clear'
  if (factor < 3.0) return 'Moderate'
  return 'Heavy'
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * @param {{ telemetry: object|null }} props
 */
export default function TelemetryPanel({ telemetry: t }) {
  if (!t) {
    return (
      <div className="telemetry-panel">
        <p className="telemetry-placeholder">Waiting for telemetry…</p>
      </div>
    )
  }

  const speedPct    = Math.min(100, (t.speed_kmh / 120) * 100)
  const progressPct = t.total_segments > 0
    ? (t.current_segment_index / t.total_segments) * 100
    : 0
  const cClass = congestionClass(t.congestion_factor)

  return (
    <div className="telemetry-panel">

      {/* ── Speed ─────────────────────────────────────────────────────── */}
      <div>
        <div className="telemetry-speed">
          <span className="telemetry-speed__value">{Math.round(t.speed_kmh)}</span>
          <span className="telemetry-speed__unit">km/h</span>
        </div>
        <div className="telemetry-bar">
          <div
            className="telemetry-bar__fill telemetry-bar__fill--speed"
            style={{ width: `${speedPct}%` }}
          />
        </div>
      </div>

      {/* ── ETA ──────────────────────────────────────────────────────── */}
      <div className="telemetry-row">
        <span className="telemetry-row__label">ETA</span>
        <span className="telemetry-eta">{fmtEta(t.eta_seconds)}</span>
      </div>

      {/* ── Road name ─────────────────────────────────────────────────── */}
      <div className="telemetry-row">
        <span className="telemetry-row__label">Road</span>
        <span className="telemetry-road" title={t.current_road_name}>
          {t.current_road_name || 'Unknown'}
        </span>
      </div>

      {/* ── Distance remaining ────────────────────────────────────────── */}
      <div className="telemetry-row">
        <span className="telemetry-row__label">Remaining</span>
        <span className="telemetry-row__value">{fmtDist(t.distance_remaining_m)}</span>
      </div>

      {/* ── Route progress ───────────────────────────────────────────── */}
      <div>
        <div className="telemetry-row" style={{ marginBottom: 4 }}>
          <span className="telemetry-row__label">Progress</span>
          <span className="telemetry-row__value" style={{ fontSize: 11 }}>
            seg {t.current_segment_index} / {t.total_segments}
          </span>
        </div>
        <div className="telemetry-bar">
          <div
            className="telemetry-bar__fill telemetry-bar__fill--progress"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* ── Congestion ───────────────────────────────────────────────── */}
      <div className="telemetry-row">
        <span className="telemetry-row__label">Congestion</span>
        <span className={`telemetry-row__value ${cClass}`}>
          {t.congestion_factor.toFixed(1)}× — {congestionLabel(t.congestion_factor)}
        </span>
      </div>

      {/* ── Reroutes ──────────────────────────────────────────────────── */}
      <div className="telemetry-row">
        <span className="telemetry-row__label">Reroutes</span>
        <span className="telemetry-row__value">{t.reroute_count}</span>
      </div>

      {/* ── Signal ahead ─────────────────────────────────────────────── */}
      {t.is_near_signal && (
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <span className="signal-badge">
            <span className="signal-dot" />
            Signal Ahead
          </span>
        </div>
      )}
    </div>
  )
}
