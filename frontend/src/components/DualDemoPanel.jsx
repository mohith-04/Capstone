/**
 * DualDemoPanel.jsx — Control panel for the dual-ambulance conflict demo
 * ========================================================================
 *
 * Shows in the sidebar when the user switches to Dual Demo mode.
 * Provides:
 *   - Severity selectors for each ambulance
 *   - Three scenario launch buttons (ETA / Severity / Timestamp)
 *   - Live status for both ambulances
 *   - Stop Both button
 *
 * Props
 * -----
 *   dual   object   Return value of useDualSimulation()
 */

import { useState } from 'react'
import './DualDemoPanel.css'

const STATE_COLORS = {
  idle:     '#8b949e',
  starting: '#58a6ff',
  running:  '#3fb950',
  arrived:  '#a371f7',
  error:    '#f85149',
}

const STATE_DOT = {
  idle:     '○',
  starting: '◌',
  running:  '●',
  arrived:  '★',
  error:    '✕',
}

function AmbBadge({ id, accentColor, simState }) {
  const c = STATE_COLORS[simState] || '#8b949e'
  return (
    <div className="dual-panel__amb-badge" style={{ borderColor: accentColor }}>
      <span className="dual-panel__amb-id" style={{ background: accentColor }}>🚑 {id}</span>
      <span style={{ color: c, fontSize: 11, fontWeight: 600 }}>
        {STATE_DOT[simState]} {simState}
      </span>
    </div>
  )
}

export default function DualDemoPanel({ dual }) {
  const [sevA, setSevA] = useState('critical')
  const [sevB, setSevB] = useState('serious')

  const { sim1, sim2, anyRunning, bothIdle, startBoth, stopBoth } = dual

  return (
    <div className="dual-panel">
      {/* ── Live ambulance status ─────────────────────────────────────── */}
      <div className="dual-panel__status-row">
        <AmbBadge id="AMB-001" accentColor="#58a6ff" simState={sim1.simState} />
        <AmbBadge id="AMB-002" accentColor="#a371f7" simState={sim2.simState} />
      </div>

      {/* ── Severity selectors ───────────────────────────────────────── */}
      <div className="dual-panel__section">
        <div className="dual-panel__section-title">Case Severity</div>
        <div className="dual-panel__sev-row">
          <div className="dual-panel__sev-col">
            <label style={{ color: '#58a6ff' }}>AMB-001</label>
            <select
              className="dual-panel__select"
              value={sevA}
              onChange={(e) => setSevA(e.target.value)}
              disabled={anyRunning}
            >
              <option value="critical">🔴 Critical</option>
              <option value="serious">🟠 Serious</option>
              <option value="moderate">🟡 Moderate</option>
            </select>
          </div>
          <div className="dual-panel__sev-col">
            <label style={{ color: '#a371f7' }}>AMB-002</label>
            <select
              className="dual-panel__select"
              value={sevB}
              onChange={(e) => setSevB(e.target.value)}
              disabled={anyRunning}
            >
              <option value="critical">🔴 Critical</option>
              <option value="serious">🟠 Serious</option>
              <option value="moderate">🟡 Moderate</option>
            </select>
          </div>
        </div>
        <p className="dual-panel__sev-note">
          Severity is used as a tiebreaker when both ambulances have similar ETAs.
        </p>
      </div>

      {/* ── Scenario buttons ─────────────────────────────────────────── */}
      {bothIdle && (
        <div className="dual-panel__section">
          <div className="dual-panel__section-title">Launch Scenario</div>

          <button
            className="dual-panel__scenario-btn"
            onClick={() => startBoth('eta', sevA, sevB)}
          >
            <div className="dual-panel__scenario-header">
              <span className="dual-panel__scenario-icon">📏</span>
              <span className="dual-panel__scenario-name">Scenario 1 — ETA Decides</span>
            </div>
            <div className="dual-panel__scenario-desc">
              AMB-001 starts far (Benz Circle), AMB-002 starts close to the
              intersection from the north. Lower ETA wins — severity ignored.
            </div>
            <div className="dual-panel__scenario-tip">
              💡 Expected winner: <strong>AMB-002</strong> (lower ETA)
            </div>
          </button>

          <button
            className="dual-panel__scenario-btn"
            onClick={() => startBoth('severity', sevA, sevB)}
          >
            <div className="dual-panel__scenario-header">
              <span className="dual-panel__scenario-icon">⚡</span>
              <span className="dual-panel__scenario-name">Scenario 2 — Severity Decides</span>
            </div>
            <div className="dual-panel__scenario-desc">
              Both ambulances are timed to arrive at the conflict signal
              simultaneously. Since ETAs are equal, the severity you set above
              determines the winner.
            </div>
            <div className="dual-panel__scenario-tip">
              💡 Expected winner: ambulance with <strong>higher severity</strong>
            </div>
          </button>

          <button
            className="dual-panel__scenario-btn"
            onClick={() => startBoth('timestamp', sevA, sevB)}
          >
            <div className="dual-panel__scenario-header">
              <span className="dual-panel__scenario-icon">🕐</span>
              <span className="dual-panel__scenario-name">Scenario 3 — Request Time Decides</span>
            </div>
            <div className="dual-panel__scenario-desc">
              Both equidistant, both Critical severity. AMB-001 request is fired
              300ms before AMB-002 — first requester wins.
            </div>
            <div className="dual-panel__scenario-tip">
              💡 Expected winner: <strong>AMB-001</strong> (earlier request)
            </div>
          </button>
        </div>
      )}

      {/* ── Running controls ─────────────────────────────────────────── */}
      {anyRunning && (
        <div className="dual-panel__section">
          <button className="dual-panel__stop-btn" onClick={stopBoth}>
            ■ Stop Both Ambulances
          </button>
          <p className="dual-panel__running-hint">
            Watch the map — when both routes converge at the same signal,
            the Conflict Decision Card will appear automatically.
          </p>
        </div>
      )}

      {/* ── Priority logic reference ─────────────────────────────────── */}
      <div className="dual-panel__section dual-panel__section--ref">
        <div className="dual-panel__section-title">Priority Tiebreaker Order</div>
        <ol className="dual-panel__tier-list">
          <li><strong>ETA</strong> — lowest arrival time wins</li>
          <li><strong>Severity</strong> — Critical &gt; Serious &gt; Moderate</li>
          <li><strong>Distance</strong> — physically closer ambulance wins</li>
          <li><strong>Request Time</strong> — first-come, first-served</li>
        </ol>
      </div>
    </div>
  )
}
