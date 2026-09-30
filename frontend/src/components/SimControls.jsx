/**
 * SimControls.jsx — Simulation control panel ("cockpit")
 * =======================================================
 *
 * This is the main operator interface.  It fetches the hospital registry on
 * mount, lets the user configure the simulation, and exposes Start / Stop /
 * Reroute / Reset All buttons.
 *
 * Props are kept minimal — all simulation state lives in the useSimulation
 * hook; this component only triggers actions and shows the current simState.
 */

import { useState, useEffect } from 'react'
import { getHospitals, resetAlerts, resetSignals, clearCongestion } from '../api'
import './SimControls.css'

const STATE_DOT = {
  idle:     '○',
  starting: '◌',
  running:  '●',
  arrived:  '★',
  error:    '✕',
}

/**
 * @param {{
 *   simState: string,
 *   onStart: function,
 *   onStop: function,
 *   onReroute: function,
 *   onReset: function,
 *   rerouting: boolean,
 * }} props
 */
export default function SimControls({ simState, onStart, onStop, onReroute, onReset, rerouting }) {
  const [hospitals,       setHospitals]       = useState([])
  const [loadingHospitals, setLoadingHospitals] = useState(true)
  const [hospitalId,      setHospitalId]      = useState('govt-gen-hosp')
  const [severity,        setSeverity]        = useState('critical')
  const [speedMultiplier, setSpeedMultiplier] = useState(5)
  const [emergencyType,   setEmergencyType]   = useState('Road Traffic Accident')
  const [bloodType,       setBloodType]       = useState('Unknown')
  const [notes,           setNotes]           = useState('')
  // Paramedic-recorded vitals (collapsed by default)
  const [showVitals, setShowVitals] = useState(false)
  const [gcs,     setGcs]     = useState('')
  const [spo2,    setSpo2]    = useState('')
  const [hr,      setHr]      = useState('')
  const [bpSys,   setBpSys]   = useState('')
  const [bpDia,   setBpDia]   = useState('')

  // Load hospital registry once on mount
  useEffect(() => {
    getHospitals()
      .then((data) => {
        setHospitals(data.hospitals || [])
        setLoadingHospitals(false)
      })
      .catch((err) => {
        console.error('Failed to load hospitals:', err)
        setLoadingHospitals(false)
      })
  }, [])

  const isRunning = simState === 'running'
  const isIdle    = simState === 'idle' || simState === 'arrived' || simState === 'error'

  // Look up the selected hospital's full record (we need its lat/lng for routing)
  const selectedHospital = hospitals.find((h) => h.id === hospitalId) || null

  const [vitalsError, setVitalsError] = useState('')

  function handleStart() {
    if (!isIdle) return
    if (!selectedHospital) {
      console.warn('[SimControls] No hospital selected — cannot determine route endpoint')
      return
    }

    // Validate vitals ranges before sending to API
    const errors = []
    if (gcs !== '') {
      const v = Number(gcs)
      if (isNaN(v) || v < 3 || v > 15) errors.push('GCS must be between 3 and 15')
    }
    if (spo2 !== '') {
      const v = Number(spo2)
      if (isNaN(v) || v < 50 || v > 100) errors.push('SpO₂ must be between 50% and 100%')
    }
    if (hr !== '') {
      const v = Number(hr)
      if (isNaN(v) || v < 20 || v > 300) errors.push('HR must be between 20 and 300 bpm')
    }
    if (bpSys !== '') {
      const v = Number(bpSys)
      if (isNaN(v) || v < 40 || v > 300) errors.push('BP Systolic must be between 40 and 300 mmHg')
    }
    if (bpDia !== '') {
      const v = Number(bpDia)
      if (isNaN(v) || v < 20 || v > 200) errors.push('BP Diastolic must be between 20 and 200 mmHg')
    }

    if (errors.length > 0) {
      setVitalsError(errors.join(' · '))
      return
    }
    setVitalsError('')

    onStart({
      hospitalId,
      endLat: selectedHospital.lat,
      endLng: selectedHospital.lng,
      severity,
      speedMultiplier: Number(speedMultiplier),
      emergencyType,
      bloodType,
      notes,
      // Parse vitals — send null if field is empty so backend uses severity-based defaults
      gcs:    gcs    !== '' ? Number(gcs)    : null,
      spo2:   spo2   !== '' ? Number(spo2)   : null,
      hr:     hr     !== '' ? Number(hr)     : null,
      bpSys:  bpSys  !== '' ? Number(bpSys)  : null,
      bpDia:  bpDia  !== '' ? Number(bpDia)  : null,
    })
  }


  async function handleReset() {
    // Reset backend state across all three modules
    await Promise.allSettled([resetAlerts(), resetSignals(), clearCongestion()])
    onReset()
  }

  return (
    <div className="sim-controls">

      {/* ── State badge ───────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <span className={`sim-state-badge sim-state-badge--${simState}`}>
          {STATE_DOT[simState]} {simState}
        </span>
      </div>

      {/* ── Hospital selector ─────────────────────────────────────────── */}
      <div className="sim-field">
        <label htmlFor="hospital-select">Destination Hospital</label>
        {loadingHospitals ? (
          <p className="sim-loading">Loading hospitals…</p>
        ) : (
          <select
            id="hospital-select"
            className="sim-select"
            value={hospitalId}
            onChange={(e) => setHospitalId(e.target.value)}
            disabled={isRunning}
          >
            {hospitals.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* ── Severity selector ─────────────────────────────────────────── */}
      <div className="sim-field">
        <label htmlFor="severity-select">Case Severity</label>
        <select
          id="severity-select"
          className="sim-select"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          disabled={isRunning}
        >
          <option value="critical">🔴 Critical</option>
          <option value="serious"> 🟠 Serious</option>
          <option value="moderate">🟡 Moderate</option>
        </select>
      </div>

      {/* ── Emergency Type ─────────────────────────────────────────────── */}
      <div className="sim-field">
        <label htmlFor="emergency-type-select">Emergency Type</label>
        <select
          id="emergency-type-select"
          className="sim-select"
          value={emergencyType}
          onChange={(e) => setEmergencyType(e.target.value)}
          disabled={isRunning}
        >
          <option value="Road Traffic Accident">🚗 Road Traffic Accident</option>
          <option value="Heart Attack / Cardiac Arrest">❤️ Heart Attack / Cardiac Arrest</option>
          <option value="Stroke">🧠 Stroke</option>
          <option value="Traumatic Fall">🏚️ Traumatic Fall</option>
          <option value="Burns">🔥 Burns</option>
          <option value="Respiratory Distress">🫁 Respiratory Distress</option>
          <option value="Poisoning / Overdose">⚠️ Poisoning / Overdose</option>
          <option value="Obstetric Emergency">🤰 Obstetric Emergency</option>
          <option value="Unknown">❓ Unknown</option>
        </select>
      </div>

      {/* ── Blood Type ─────────────────────────────────────────────────── */}
      <div className="sim-field">
        <label htmlFor="blood-type-select">Blood Type</label>
        <select
          id="blood-type-select"
          className="sim-select"
          value={bloodType}
          onChange={(e) => setBloodType(e.target.value)}
          disabled={isRunning}
        >
          <option value="Unknown">Unknown</option>
          <option value="A+">A+</option>
          <option value="A-">A-</option>
          <option value="B+">B+</option>
          <option value="B-">B-</option>
          <option value="O+">O+</option>
          <option value="O-">O-</option>
          <option value="AB+">AB+</option>
          <option value="AB-">AB-</option>
        </select>
      </div>

      {/* ── Patient Vitals (collapsible) ────────────────────────────────── */}
      <div className="sim-field">
        <button
          type="button"
          className="sim-vitals-toggle"
          onClick={() => {
            const next = !showVitals
            setShowVitals(next)
            // Clear values when hiding so they don't silently persist
            if (!next) { setGcs(''); setSpo2(''); setHr(''); setBpSys(''); setBpDia(''); setVitalsError('') }
          }}
          disabled={isRunning}
        >
          <div className="sim-vitals-toggle-left">
            <span>{showVitals ? '▾' : '▸'}</span>
            <span>{showVitals ? 'Hide Patient Vitals' : 'Add Patient Vitals'}</span>
          </div>
          {!showVitals && <span className="sim-vitals-toggle-hint">(Optional)</span>}
        </button>

        {showVitals && (
          <>
            <div className="sim-vitals-grid">
              <div className="sim-vitals-item">
                <span>GCS (3–15)</span>
                <input type="number" min="3" max="15" placeholder="unknown" className="sim-vitals-input"
                  value={gcs} onChange={e => { setGcs(e.target.value); setVitalsError('') }} disabled={isRunning} />
              </div>
              <div className="sim-vitals-item">
                <span>SpO₂ %</span>
                <input type="number" min="50" max="100" placeholder="unknown" className="sim-vitals-input"
                  value={spo2} onChange={e => { setSpo2(e.target.value); setVitalsError('') }} disabled={isRunning} />
              </div>
              <div className="sim-vitals-item">
                <span>HR (bpm)</span>
                <input type="number" min="20" max="300" placeholder="unknown" className="sim-vitals-input"
                  value={hr} onChange={e => { setHr(e.target.value); setVitalsError('') }} disabled={isRunning} />
              </div>
              <div className="sim-vitals-item">
                <span>BP Sys (mmHg)</span>
                <input type="number" min="40" max="300" placeholder="unknown" className="sim-vitals-input"
                  value={bpSys} onChange={e => { setBpSys(e.target.value); setVitalsError('') }} disabled={isRunning} />
              </div>
              <div className="sim-vitals-item">
                <span>BP Dia (mmHg)</span>
                <input type="number" min="20" max="200" placeholder="unknown" className="sim-vitals-input"
                  value={bpDia} onChange={e => { setBpDia(e.target.value); setVitalsError('') }} disabled={isRunning} />
              </div>
            </div>
            <p className="sim-vitals-hint">Leave any unknown field blank — it will be marked as N/A in the alert.</p>
            {vitalsError && (
              <div className="sim-vitals-error">
                ⚠ {vitalsError}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Paramedic Notes ───────────────────────────────────────────── */}
      <div className="sim-field">
        <label htmlFor="notes-input">Paramedic Notes</label>
        <textarea
          id="notes-input"
          className="sim-textarea"
          rows={2}
          placeholder="e.g. Patient unconscious, severe bleeding..."
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={isRunning}
        />
      </div>

      {/* ── Speed multiplier ──────────────────────────────────────────── */}
      <div className="sim-field">
        <label>Demo Speed</label>
        <div className="sim-slider-row">
          <input
            type="range"
            className="sim-slider"
            min={1} max={20} step={1}
            value={speedMultiplier}
            onChange={(e) => setSpeedMultiplier(e.target.value)}
            disabled={isRunning}
          />
          <span className="sim-slider-value">{speedMultiplier}×</span>
        </div>
      </div>

      {/* ── Action buttons ────────────────────────────────────────────── */}
      <div className="sim-buttons">
        <button
          className="sim-btn sim-btn--start"
          onClick={handleStart}
          disabled={!isIdle || loadingHospitals}
        >
          ▶ Start Simulation
        </button>

        {isRunning && (
          <>
            <button
              className="sim-btn sim-btn--reroute"
              onClick={onReroute}
              disabled={rerouting}
            >
              {rerouting ? '↺ Rerouting…' : '↺ Inject Reroute'}
            </button>
            <button
              className="sim-btn sim-btn--stop"
              onClick={onStop}
            >
              ■ Stop
            </button>
          </>
        )}
      </div>

      {/* ── Reset all ─────────────────────────────────────────────────── */}
      <button
        className="sim-btn sim-btn--reset"
        onClick={handleReset}
        disabled={isRunning}
      >
        ↺ Reset All
      </button>
    </div>
  )
}
