/**
 * SimPane.jsx — Self-contained ambulance simulation pane
 * =======================================================
 *
 * Used in dual-ambulance demo mode.  Each pane owns one useSimulation()
 * instance, one MapView, and a compact sidebar with the most-important
 * tabs (Controls → Telemetry → Signals).
 *
 * Props
 * -----
 *   label        string   "AMB-001" or "AMB-002" — shown in the pane header
 *   accentColor  string   CSS colour used for the pane border + label badge
 *   ambulanceId  string   Passed into useSimulation so the backend gets the
 *                         right ID.
 *   startCoords  object   {lat, lng} — where this ambulance starts from.
 */

import { useState, useEffect, useRef } from 'react'
import { useSimulation }  from '../hooks/useSimulation'
import { useToasts, ToastContainer } from './Toast'
import MapView            from './MapView'
import SimControls        from './SimControls'
import TelemetryPanel     from './TelemetryPanel'
import SignalPanel        from './SignalPanel'
import MetricsPanel       from './MetricsPanel'
import './SimPane.css'

// Tabs shown in the compact dual-pane sidebar (fewer than single mode to save space)
const PANE_TABS = [
  { id: 'controls',  label: '⚙ Controls'  },
  { id: 'telemetry', label: '📡 Telemetry' },
  { id: 'signals',   label: '🚦 Signals'   },
  { id: 'metrics',   label: '📊 Metrics'   },
]

export default function SimPane({ label, accentColor, ambulanceId, startCoords }) {
  const sim = useSimulation({ ambulanceId, startCoords })
  const [activeTab, setActiveTab] = useState('controls')
  const { toasts, addToast, dismissToast } = useToasts()
  const prevAlertCount = useRef(0)

  // ── Auto-tab switching (mirrors App.jsx behaviour) ───────────────────────
  useEffect(() => {
    if (sim.simState === 'running' && activeTab === 'controls') {
      setActiveTab('telemetry')
    }
    if (sim.simState === 'arrived') {
      setActiveTab('metrics')
    }
  }, [sim.simState])

  // ── Toast on arrival ─────────────────────────────────────────────────────
  useEffect(() => {
    const diff = sim.alerts.length - prevAlertCount.current
    if (diff > 0) {
      const newAlerts = sim.alerts.slice(0, diff)
      newAlerts.reverse().forEach((alert) => {
        if (alert.event === 'update') return
        addToast({
          type: alert.event === 'arrival' ? 'arrived' : 'initial',
          text: alert.event === 'arrival'
            ? `${label} arrived at ${alert.hospital_name}`
            : `${label}: pre-alert → ${alert.hospital_name}`,
          sub: alert.eta_s != null ? `ETA ${Math.round(alert.eta_s)}s` : '',
        })
      })
    }
    prevAlertCount.current = sim.alerts.length
  }, [sim.alerts.length])

  // ── handleStart — passes the fixed ambulanceId from props ────────────────
  function handleStart(params) {
    prevAlertCount.current = 0
    sim.start({
      ambulanceId,
      hospitalId:      params.hospitalId,
      endLat:          params.endLat,
      endLng:          params.endLng,
      severity:        params.severity,
      speedMultiplier: params.speedMultiplier,
      emergencyType:   params.emergencyType,
      bloodType:       params.bloodType,
      notes:           params.notes,
      gcs:             params.gcs,
      spo2:            params.spo2,
      hr:              params.hr,
      bpSys:           params.bpSys,
      bpDia:           params.bpDia,
    })
  }

  function handleReset() {
    setActiveTab('controls')
  }

  const signalActive = sim.signals.some((s) => s.mode === 'priority_active')

  return (
    <div className="sim-pane" style={{ '--pane-accent': accentColor }}>
      {/* Toast layer (scoped to this pane) */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* ── Pane header ─────────────────────────────────────────────────── */}
      <div className="sim-pane__header">
        <span className="sim-pane__label" style={{ background: accentColor }}>
          🚑 {label}
        </span>
        <span className={`sim-pane__state sim-pane__state--${sim.simState}`}>
          {sim.simState === 'running'  && '● Live'}
          {sim.simState === 'starting' && '◌ Starting…'}
          {sim.simState === 'arrived'  && '✓ Arrived'}
          {sim.simState === 'error'    && '✕ Error'}
          {sim.simState === 'idle'     && '○ Idle'}
        </span>
        {signalActive && (
          <span className="sim-pane__signal-badge">🚦 Signal Preempted</span>
        )}
      </div>

      {/* ── Map ─────────────────────────────────────────────────────────── */}
      <div className="sim-pane__map">
        <MapView
          route={sim.route}
          telemetry={sim.telemetry}
          signals={sim.signals}
          hospital={sim.hospital}
          simState={sim.simState}
        />
      </div>

      {/* ── Compact sidebar ─────────────────────────────────────────────── */}
      <div className="sim-pane__sidebar">
        {/* Tab bar */}
        <div className="sim-pane__tabbar">
          {PANE_TABS.map(({ id, label: tabLabel }) => (
            <button
              key={id}
              className={`sim-pane__tab ${activeTab === id ? 'sim-pane__tab--active' : ''}`}
              onClick={() => setActiveTab(id)}
            >
              {tabLabel}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="sim-pane__content">
          {activeTab === 'controls' && (
            <SimControls
              simState={sim.simState}
              onStart={handleStart}
              onStop={sim.stop}
              onReroute={sim.triggerReroute}
              onReset={handleReset}
              rerouting={sim.rerouting}
            />
          )}
          {activeTab === 'telemetry' && (
            <TelemetryPanel telemetry={sim.telemetry} />
          )}
          {activeTab === 'signals' && (
            <SignalPanel signals={sim.signals} />
          )}
          {activeTab === 'metrics' && (
            <MetricsPanel
              arrivedData={sim.arrivedData}
              telemetry={sim.telemetry}
              simState={sim.simState}
              startTimeRef={sim.startTimeRef}
            />
          )}
        </div>
      </div>
    </div>
  )
}
