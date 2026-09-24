/**
 * DemoScript.jsx — Presenter's live script / viva cheat sheet
 * ============================================================
 *
 * Shows the demo walkthrough as numbered steps.
 * Each step automatically becomes "active" based on the current simState and
 * alert/signal events.  This gives the presenter a live guide during the
 * 10-minute project demo without needing a separate script document.
 *
 * Viva panels typically ask: "How does this differ from existing ambulance GPS?"
 * The "talking point" on each step is the rehearsed answer for that moment.
 */

import './DemoScript.css'

const STEPS = [
  {
    id: 'start',
    title: 'Start Simulation',
    hint: 'Select hospital, severity=Critical, speed=5×, click Start.',
    talkingPoint: 'The backend runs A* on a real OSMnx graph of Vijayawada — 20,962 nodes, 54,359 edges.',
    activeWhen: (s) => s.simState === 'idle',
  },
  {
    id: 'route',
    title: 'Route on Map',
    hint: 'Blue polyline appears from Benz Circle → Kanaka Durga Temple (5.4 km).',
    talkingPoint: 'Edge weights combine road speed, congestion, signal-wait penalty, and hazard flags — not just distance.',
    activeWhen: (s) => s.simState === 'running' && !s.hasAlert && !s.signalActive,
  },
  {
    id: 'signal',
    title: 'Signal Priority',
    hint: 'Watch Signals tab — badge turns green PRIORITY ACTIVE when ambulance approaches.',
    talkingPoint: 'Our FSM (4 states) cuts the current red phase short only after the minimum green time (10s IRC:103) to prevent cross-traffic accidents.',
    activeWhen: (s) => s.signalActive,
  },
  {
    id: 'alert',
    title: 'Hospital Pre-Alert',
    hint: 'At 2km: blue "PRE-ALERT SENT" card. At 1km: yellow "ETA UPDATE".',
    talkingPoint: 'MIST-format vitals (Mechanism, Injuries, Signs, Treatment) are generated deterministically — same ambulance ID always gives same patient profile for repeatable demos.',
    activeWhen: (s) => s.hasAlert && s.simState === 'running',
  },
  {
    id: 'reroute',
    title: 'Inject Reroute (Optional)',
    hint: 'Click "Inject Reroute" — congestion appears, route recomputes live.',
    talkingPoint: 'The ambulance never teleports backward. We splice the new A* path at the closest node to the current position.',
    activeWhen: (s) => false,  // manual step — highlight on demand
  },
  {
    id: 'metrics',
    title: 'Mission Complete',
    hint: 'See Metrics tab: before/after comparison and signal time saved.',
    talkingPoint: 'Without our system: avg 25 km/h urban speed (AIIMS 2023 study). With our system: 40 km/h + signal preemption. The delta is the patient-outcome improvement we\'re selling.',
    activeWhen: (s) => s.simState === 'arrived',
  },
]

function stepStatus(step, context) {
  if (step.activeWhen(context)) return 'active'
  // Steps before the current active step are "done"
  const activeIdx = STEPS.findIndex((s) => s.activeWhen(context))
  const myIdx = STEPS.indexOf(step)
  if (activeIdx > myIdx) return 'done'
  return 'pending'
}

/**
 * @param {{
 *   simState: string,
 *   hasAlert: boolean,
 *   signalActive: boolean,
 * }} props
 */
export default function DemoScript({ simState, hasAlert, signalActive }) {
  const context = { simState, hasAlert, signalActive }

  return (
    <div className="demo-script">
      {STEPS.map((step) => {
        const status = stepStatus(step, context)
        return (
          <div key={step.id} className={`demo-step demo-step--${status}`}>
            <div className="demo-step__num">
              {status === 'done' ? '✓' : STEPS.indexOf(step) + 1}
            </div>
            <div className="demo-step__content">
              <div className="demo-step__title">{step.title}</div>
              <div className="demo-step__hint">{step.hint}</div>
              {status === 'active' && (
                <div className="demo-talking-point">
                  💬 {step.talkingPoint}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
