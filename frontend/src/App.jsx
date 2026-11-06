/**
 * App.jsx — Root application component
 * ======================================
 *
 * Supports two demo modes toggled from the header:
 *
 *   Single mode (default)
 *     The original layout — one ambulance, full map, full tabbed sidebar.
 *
 *   Dual mode
 *     A single shared map showing both ambulances simultaneously.
 *     A DualDemoPanel sidebar controls three hardcoded conflict scenarios:
 *       1. ETA decides — one ambulance arrives at the conflict node sooner
 *       2. Severity decides — equal ETA, severity is tiebreaker
 *       3. Request time decides — both critical+equal, first request wins
 *     A ConflictDecisionCard floats over the map when a conflict is active,
 *     explaining WHY a winner was chosen.
 *
 * Single-mode layout (unchanged):
 *   Header   — title + mode toggle + backend connection badge
 *   Main:
 *     Left   — MapView (flex:1)
 *     Right  — 320px tabbed sidebar
 *
 * Dual-mode layout:
 *   Header   — same header with mode toggle
 *   Main:
 *     Left   — MapView showing BOTH ambulances on one shared map (flex:1)
 *     Right  — 320px sidebar with DualDemoPanel scenario controls
 */

import { useState, useEffect, useRef } from 'react'
import { checkHealth }         from './api'
import { useSimulation }       from './hooks/useSimulation'
import { useDualSimulation }   from './hooks/useDualSimulation'
import { useToasts, ToastContainer } from './components/Toast'
import MapView                 from './components/MapView'
import SimControls             from './components/SimControls'
import TelemetryPanel          from './components/TelemetryPanel'
import SignalPanel             from './components/SignalPanel'
import AlertPanel              from './components/AlertPanel'
import MetricsPanel            from './components/MetricsPanel'
import DemoScript              from './components/DemoScript'
import DualDemoPanel           from './components/DualDemoPanel'
import ConflictDecisionCard    from './components/ConflictDecisionCard'

import 'leaflet/dist/leaflet.css'
import './App.css'

// ── Status badge ────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const cfg = {
    connected: { bg: '#1a3a1a', fg: '#3fb950', border: '#238636', label: '● Connected'          },
    error:     { bg: '#3a1a1a', fg: '#f85149', border: '#6e2c2c', label: '✕ Backend Unreachable' },
    loading:   { bg: '#1a2a3a', fg: '#58a6ff', border: '#1f6feb', label: '◌ Connecting…'         },
  }
  const c = cfg[status]
  return (
    <span style={{
      background: c.bg, color: c.fg, border: `1px solid ${c.border}`,
      padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 600,
      letterSpacing: '0.02em', whiteSpace: 'nowrap',
    }}>
      {c.label}
    </span>
  )
}

// ── Sim state badge ───────────────────────────────────────────────────────────

function SimStateBadge({ simState }) {
  if (simState === 'idle') return null
  const cfg = {
    starting: { color: '#58a6ff', label: 'Starting…' },
    running:  { color: '#3fb950', label: '● Live' },
    arrived:  { color: '#a371f7', label: '✓ Complete' },
    error:    { color: '#f85149', label: '✕ Error' },
  }
  const c = cfg[simState]
  if (!c) return null
  return (
    <span style={{
      color: c.color, fontSize: 12, fontWeight: 700,
      padding: '2px 10px', border: `1px solid ${c.color}33`,
      borderRadius: 20, background: `${c.color}11`,
    }}>
      {c.label}
    </span>
  )
}

// ── Tab bar ──────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'controls',  label: 'Controls' },
  { id: 'telemetry', label: 'Telemetry' },
  { id: 'signals',   label: 'Signals' },
  { id: 'alerts',    label: 'Alerts' },
  { id: 'metrics',   label: 'Metrics' },
  { id: 'demo',      label: '📋 Demo' },
]

function TabBar({ active, onChange, alertCount }) {
  return (
    <div className="tab-bar">
      {TABS.map(({ id, label }) => (
        <button
          key={id}
          className={`tab-btn ${active === id ? 'tab-btn--active' : ''}`}
          onClick={() => onChange(id)}
        >
          {label}
          {id === 'alerts' && alertCount > 0 && (
            <span className="tab-badge">{alertCount}</span>
          )}
        </button>
      ))}
    </div>
  )
}

// ── Section card ─────────────────────────────────────────────────────────────

function SectionCard({ title, children }) {
  return (
    <div className="info-card">
      <h3 className="info-card__title">{title}</h3>
      {children}
    </div>
  )
}

// ── Alert event → toast config ────────────────────────────────────────────────

function alertToToast(event, hospitalName, etaS, distM) {
  const dist = distM != null ? `${(distM / 1000).toFixed(1)} km away` : ''
  const eta  = etaS  != null ? `ETA ${Math.round(etaS)}s` : ''
  const sub  = [dist, eta].filter(Boolean).join(' · ')

  const config = {
    initial: {
      type: 'initial',
      text: `Pre-alert sent to ${hospitalName}`,
    },
    update: {
      type: 'update',
      text: `ETA update sent to ${hospitalName}`,
    },
    arrival: {
      type: 'arrival',
      text: `Ambulance arrived at ${hospitalName}`,
    },
  }
  return { ...(config[event] || config.initial), sub }
}

// ── Root component ───────────────────────────────────────────────────────────

export default function App({ ambulanceId = 'AMB-001', user = null, onLogout = null }) {
  const [connectionStatus, setConnectionStatus] = useState('loading')
  const [activeTab,        setActiveTab]        = useState('controls')
  // 'single' = classic one-ambulance view | 'dual' = split two-ambulance view
  const [demoMode,         setDemoMode]         = useState('single')

  // ── Backend health check ─────────────────────────────────────────────────
  useEffect(() => {
    checkHealth()
      .then(() => setConnectionStatus('connected'))
      .catch(() => setConnectionStatus('error'))
  }, [])

  // ── Simulation hook (single-ambulance mode only) ─────────────────────────
  // In dual mode the two SimPane components each own their own hook instance.
  const sim = useSimulation({ ambulanceId })

  // ── Dual-ambulance simulation hook ───────────────────────────────────────
  // Always called unconditionally (React hooks rule) — idle when not in use.
  const dual = useDualSimulation()

  // ── Toast system ─────────────────────────────────────────────────────────
  const { toasts, addToast, dismissToast } = useToasts()
  const prevAlertCount = useRef(0)

  // Fire toasts for new alert events
  useEffect(() => {
    // sim.alerts is prepended (newest first). To get the newly added alerts,
    // we take the first N items where N is the difference in length.
    const diff = sim.alerts.length - prevAlertCount.current
    if (diff > 0) {
      const newAlerts = sim.alerts.slice(0, diff)
      // Reverse so they toast in chronological order if multiple arrived at once
      newAlerts.reverse().forEach((alert) => {
        // Only toast the initial pre-alert (2km) and arrival (50m) to avoid double notifications
        if (alert.event === 'update') return

        const toastCfg = alertToToast(
          alert.event,
          alert.hospital_name,
          alert.eta_s,
          alert.distance_m
        )
        addToast(toastCfg)
      })
    }
    prevAlertCount.current = sim.alerts.length
  }, [sim.alerts.length, sim.alerts, addToast])

  // Mission complete toast
  useEffect(() => {
    if (sim.simState === 'arrived' && sim.arrivedData) {
      const stats  = sim.arrivedData.signal_stats || {}
      const timing = sim.arrivedData.timing || {}
      addToast({
        type: 'arrived',
        text: `Arrived in ${timing.real_world_equiv_s != null
          ? `~${Math.round(timing.real_world_equiv_s)}s`
          : 'unknown time'} — ${stats.total_preemptions || 0} signal preemptions`,
        sub: `Signal time saved: ${(stats.total_time_saved_s || 0).toFixed(1)}s`,
      })
    }
  }, [sim.simState, sim.arrivedData, addToast])

  // ── Auto-tab switching ────────────────────────────────────────────────────

  useEffect(() => {
    if (sim.simState === 'running' && activeTab === 'controls') {
      setActiveTab('telemetry')
    }
    if (sim.simState === 'arrived') {
      setActiveTab('metrics')
    }
  }, [sim.simState])

  // ── Helper computed values ────────────────────────────────────────────────

  // True if any signal is in priority_active mode (for DemoScript + tab badge)
  const signalActive = sim.signals.some((s) => s.mode === 'priority_active')
  const hasAlert     = sim.alerts.length > 0

  // ── Handlers ─────────────────────────────────────────────────────────────

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

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="app">
      {/* ── Toast notifications (float over map) ─────────────────────── */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="app-header">
        <div className="app-header__left">
          <div>
            <h1 className="app-header__title">AI Ambulance Route Optimization</h1>
            <p className="app-header__sub">
              Smart Traffic Signal Priority · Hospital Pre-Alerting · Vijayawada
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <SimStateBadge simState={sim.simState} />
          {/* ── Demo mode toggle ─────────────────────────────────────── */}
          <button
            onClick={() => setDemoMode((m) => m === 'single' ? 'dual' : 'single')}
            style={{
              padding: '4px 12px',
              borderRadius: 20,
              border: '1px solid',
              borderColor: demoMode === 'dual' ? '#a371f7' : 'var(--color-border)',
              background:   demoMode === 'dual' ? '#2a1a4a' : 'transparent',
              color:        demoMode === 'dual' ? '#a371f7' : 'var(--color-text-secondary)',
              fontSize: 12,
              fontWeight: 700,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              transition: 'all 0.2s',
            }}
            title={demoMode === 'single' ? 'Switch to Dual-Ambulance Demo' : 'Switch to Single-Ambulance View'}
          >
            {demoMode === 'dual' ? '🚑🚑 Dual Mode' : '🚑 Single Mode'}
          </button>
          <StatusBadge status={connectionStatus} />
          {user && (
            <span style={{ fontSize: 12, color: '#8b949e' }}>
              {user.unit_id} · {user.org_name}
            </span>
          )}
          {onLogout && (
            <button onClick={onLogout} style={{
              background: 'none', border: '1px solid #30363d', color: '#8b949e',
              borderRadius: 6, padding: '3px 10px', fontSize: 12, cursor: 'pointer',
            }}>Sign out</button>
          )}
        </div>
      </header>

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <main className="app-main">

        {/* ── Dual-ambulance demo mode ──────────────────────────────────── */}
        {demoMode === 'dual' && (
          <>
            {/* Single shared map — both ambulances on one view */}
            <section className="app-map-area" style={{ flex: 1 }}>
              <MapView
                route={dual.sim1.route}
                telemetry={dual.sim1.telemetry}
                signals={dual.combinedSignals}
                hospital={dual.demoHospital}
                simState={dual.sim1.simState}
                route2={dual.sim2.route}
                telemetry2={dual.sim2.telemetry}
              />
              {/* Conflict decision card floats over the map */}
              <ConflictDecisionCard conflict={dual.activeConflict} />
            </section>

            {/* Dual demo sidebar */}
            <aside className="app-sidebar">
              <div className="tab-bar">
                <span style={{ padding: '9px 12px', fontSize: 11, fontWeight: 700, color: '#a371f7' }}>
                  🚑🚑 Dual Ambulance Demo
                </span>
              </div>
              <div className="sidebar-content">
                <SectionCard title="Conflict Scenario">
                  <DualDemoPanel dual={dual} />
                </SectionCard>
              </div>
            </aside>
          </>
        )}

        {/* ── Single-ambulance mode (original layout) ───────────────────── */}
        {demoMode === 'single' && (<>

        {/* Map area */}
        <section className="app-map-area">
          <MapView
            route={sim.route}
            telemetry={sim.telemetry}
            signals={sim.signals}
            hospital={sim.hospital}
            simState={sim.simState}
          />
        </section>

        {/* Sidebar */}
        <aside className="app-sidebar">
          <TabBar
            active={activeTab}
            onChange={setActiveTab}
            alertCount={sim.alerts.length}
          />

          <div className="sidebar-content">

            {activeTab === 'controls' && (
              <SectionCard title="Simulation">
                <SimControls
                  simState={sim.simState}
                  onStart={handleStart}
                  onStop={sim.stop}
                  onReroute={sim.triggerReroute}
                  onReset={handleReset}
                  rerouting={sim.rerouting}
                />
              </SectionCard>
            )}

            {activeTab === 'telemetry' && (
              <SectionCard title="Live Telemetry">
                <TelemetryPanel telemetry={sim.telemetry} />
              </SectionCard>
            )}

            {activeTab === 'signals' && (
              <SectionCard title="Signal Priority">
                <SignalPanel signals={sim.signals} />
              </SectionCard>
            )}

            {activeTab === 'alerts' && (
              <SectionCard title="Hospital Pre-Alerts">
                <AlertPanel alerts={sim.alerts} hospital={sim.hospital} />
              </SectionCard>
            )}

            {activeTab === 'metrics' && (
              <SectionCard title="Performance Metrics">
                <MetricsPanel
                  arrivedData={sim.arrivedData}
                  telemetry={sim.telemetry}
                  simState={sim.simState}
                  startTimeRef={sim.startTimeRef}
                />
              </SectionCard>
            )}

            {activeTab === 'demo' && (
              <SectionCard title="Demo Script">
                <DemoScript
                  simState={sim.simState}
                  hasAlert={hasAlert}
                  signalActive={signalActive}
                />
              </SectionCard>
            )}

          </div>
        </aside>
        </>)}
      </main>
    </div>
  )
}
