import { useEffect, useRef, useState, useCallback } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from 'react-leaflet'
import { removeMember, rotateCode, getTrips, orgSocketUrl, getCapacity, putCapacity } from '../session'
import './HospitalDashboard.css'

const CENTER = [16.5062, 80.648]   // matches the simulated city area (Vijayawada)
const SEV = { critical: '#f85149', serious: '#d29922', stable: '#3fb950' }

const fmtEta = (s) => (s == null ? '—' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} m ${Math.round(s % 60)} s`)
const fmtDist = (m) => (m == null ? '—' : m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`)
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const o = ctx.createOscillator(), g = ctx.createGain()
    o.frequency.value = 880; g.gain.value = 0.08
    o.connect(g); g.connect(ctx.destination)
    o.start(); o.stop(ctx.currentTime + 0.25)
    setTimeout(() => ctx.close(), 400)
  } catch { /* audio blocked until the user interacts with the page */ }
}

function FlyTo({ target }) {
  const map = useMap()
  useEffect(() => { if (target) map.flyTo(target, 15, { duration: 0.8 }) }, [target, map])
  return null
}

function describeEvent(e) {
  switch (e.type) {
    case 'online':  return { text: `${e.unit_id} is now live`, tone: 'ok' }
    case 'offline': return { text: `${e.unit_id} went offline`, tone: 'muted' }
    case 'reroute': return { text: `${e.unit_id} rerouted (#${e.count})`, tone: 'warn' }
    case 'arrived': return { text: `${e.unit_id} arrived at hospital`, tone: 'ok' }
    case 'alert':
      return e.alert.event === 'arrival'
        ? { text: `${e.unit_id} reached ${e.alert.hospital_name}`, tone: 'ok' }
        : { text: `Pre-alert: ${e.unit_id} · ${e.alert.emergency_type} · ETA ${fmtEta(e.alert.eta_s)}`, tone: 'bad' }
    default: return null
  }
}

function Vitals({ v }) {
  if (!v) return null
  return (
    <div className="hd-vitals">
      {v.gcs != null && <span>GCS {v.gcs}</span>}
      {v.spo2 != null && <span>SpO₂ {v.spo2}%</span>}
      {v.hr != null && <span>HR {v.hr}</span>}
      {v.bp_sys != null && <span>BP {v.bp_sys}/{v.bp_dia}</span>}
    </div>
  )
}

/** Capacity report form — what crews' smart routing reads when choosing a destination. */
function CapacityForm({ token }) {
  const [info, setInfo] = useState(null)
  const [f, setF] = useState(null)
  const [claim, setClaim] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getCapacity(token).then((c) => {
      setInfo(c)
      const s = c.status
      setF(s && {
        er_beds_total: s.er_beds_total, er_beds_available: s.er_beds_available,
        icu_available: s.icu_available, diversion: s.diversion, specialties: s.specialties,
      })
      if (!c.hospital_id && c.claimable[0]) setClaim(c.claimable[0].id)
    }).catch((e) => setMsg(e.message))
  }, [token])

  if (!info) return <div className="hd-empty">{msg || 'Loading…'}</div>

  // First visit: pick which registry hospital this org represents.
  const form = f ?? { er_beds_total: 10, er_beds_available: 10, icu_available: 5, diversion: false, specialties: [] }
  const set = (k, v) => setF({ ...form, [k]: v })
  const toggle = (s) => set('specialties', form.specialties.includes(s)
    ? form.specialties.filter((x) => x !== s) : [...form.specialties, s])
  const num = (k) => (e) => set(k, Math.max(0, parseInt(e.target.value || '0', 10)))

  async function save(e) {
    e.preventDefault()
    setBusy(true); setMsg('')
    try {
      const c = await putCapacity(token, { ...form, hospital_id: info.hospital_id ? undefined : claim })
      setInfo(c); setF({ ...c.status })
      setMsg('Saved — crews see this immediately.')
    } catch (err) { setMsg(err.message) } finally { setBusy(false) }
  }

  const s = info.status
  return (
    <form className="hd-cap" onSubmit={save}>
      {!info.hospital_id ? (
        <label>Which hospital does your organisation run?
          <select value={claim} onChange={(e) => setClaim(e.target.value)}>
            {info.claimable.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        </label>
      ) : (
        <div className="hd-card__sub"><b>{info.hospital}</b>
          {s && (s.reported ? ` · updated ${fmtTime(s.updated_at)}` : ' · not reported yet — showing defaults')}
        </div>
      )}

      <label className={`hd-diversion ${form.diversion ? 'on' : ''}`}>
        <input type="checkbox" checked={form.diversion} onChange={(e) => set('diversion', e.target.checked)} />
        <span><b>Diversion</b> — not accepting new ambulance patients</span>
      </label>

      <div className="hd-cap__nums">
        <label>ER beds total<input type="number" min="0" value={form.er_beds_total} onChange={num('er_beds_total')} /></label>
        <label>ER beds free<input type="number" min="0" value={form.er_beds_available} onChange={num('er_beds_available')} /></label>
        <label>ICU beds free<input type="number" min="0" value={form.icu_available} onChange={num('icu_available')} /></label>
      </div>

      <div>
        <div className="hd-card__sub">Specialties available right now</div>
        <div className="hd-chips">
          {info.all_specialties.map((sp) => (
            <button type="button" key={sp} className={form.specialties.includes(sp) ? 'on' : ''}
              onClick={() => toggle(sp)}>{sp}</button>
          ))}
        </div>
      </div>

      {msg && <div className="hd-cap__msg">{msg}</div>}
      <button className="hd-save" disabled={busy}>{busy ? 'Saving…' : 'Save capacity report'}</button>
      <div className="hd-card__sub">Reports older than 6 hours are treated as “not reported” by crew routing, so keep this current.</div>
    </form>
  )
}

export default function HospitalDashboard({ session, onLogout }) {
  const token = session.token
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [live, setLive] = useState('connecting')   // connecting | live | reconnecting
  const [focus, setFocus] = useState(null)
  const [copied, setCopied] = useState(false)
  const [tab, setTab] = useState('fleet')
  const [alerts, setAlerts] = useState([])
  const [unread, setUnread] = useState(0)
  const [toasts, setToasts] = useState([])
  const [history, setHistory] = useState(null)
  const tabRef = useRef(tab)
  useEffect(() => { tabRef.current = tab }, [tab])

  const loadHistory = useCallback(() => {
    getTrips(token).then(setHistory).catch((e) => setError(e.message))
  }, [token])

  const pushToast = useCallback((text, tone) => {
    const id = Math.random()
    setToasts((t) => [...t.slice(-3), { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000)
  }, [])

  // ── Live WebSocket feed with auto-reconnect ──────────────────────────────
  useEffect(() => {
    let ws, timer, closed = false
    function connect() {
      ws = new WebSocket(orgSocketUrl(token))
      ws.onopen = () => { setLive('live'); setError('') }
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data)
        if (msg.type === 'snapshot') setData(msg.data)
        else if (msg.type === 'alerts') setAlerts(msg.data)
        else if (msg.type === 'event') {
          const e = msg.data
          const d = describeEvent(e)
          if (d) pushToast(d.text, d.tone)
          if (e.type === 'alert') {
            setAlerts((a) => [e, ...a].slice(0, 50))
            if (tabRef.current !== 'alerts') setUnread((n) => n + 1)
            beep()
          }
          if (e.type === 'arrived') loadHistory()
        }
      }
      ws.onclose = (ev) => {
        if (closed) return
        if (ev.code === 4401) { onLogout(); return }   // token rejected
        setLive('reconnecting')
        timer = setTimeout(connect, 2000)
      }
    }
    connect()
    return () => { closed = true; clearTimeout(timer); ws?.close() }
  }, [token, onLogout, pushToast, loadHistory])

  function openTab(t) {
    setTab(t)
    if (t === 'alerts') setUnread(0)
    if (t === 'history') loadHistory()
  }

  async function remove(a) {
    if (!window.confirm(`Remove ${a.unit_id} from your organisation?`)) return
    await removeMember(token, a.id).catch((e) => setError(e.message))
  }
  async function rotate() {
    if (!window.confirm('Issue a new join code? The old code will stop working.')) return
    await rotateCode(token).catch((e) => setError(e.message))
  }
  function copyCode() {
    navigator.clipboard?.writeText(data.org.join_code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
  }

  const ambs = data?.ambulances ?? []
  const online = ambs.filter((a) => a.online)
  const lastAlert = {}
  for (const e of alerts) if (e.alert.event === 'initial' && !lastAlert[e.unit_id]) lastAlert[e.unit_id] = e.alert

  return (
    <div className="hd">
      <header className="hd-header">
        <div>
          <h1>🏥 {data?.org.name ?? session.user.org_name}</h1>
          <p>
            Fleet dashboard · {online.length} of {ambs.length} ambulances live ·{' '}
            <span className={`hd-conn hd-conn--${live}`}>
              {live === 'live' ? '● Live feed' : live === 'connecting' ? '◌ Connecting…' : '◌ Reconnecting…'}
            </span>
          </p>
        </div>
        <div className="hd-header__right">
          {data && (
            <div className="hd-code">
              <span>Join code</span>
              <code>{data.org.join_code}</code>
              <button onClick={copyCode}>{copied ? 'Copied' : 'Copy'}</button>
              <button onClick={rotate} title="Invalidate this code and make a new one">New</button>
            </div>
          )}
          <button className="hd-logout" onClick={onLogout}>Sign out</button>
        </div>
      </header>

      {error && <div className="hd-error">{error}</div>}

      <div className="hd-toasts">
        {toasts.map((t) => <div key={t.id} className={`hd-toast hd-toast--${t.tone}`}>{t.text}</div>)}
      </div>

      <main className="hd-main">
        <section className="hd-map">
          <MapContainer center={CENTER} zoom={13} style={{ height: '100%', width: '100%' }}>
            <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            <FlyTo target={focus} />
            {online.filter((a) => a.live).map((a) => (
              <CircleMarker key={a.id} center={[a.live.lat, a.live.lng]} radius={10}
                pathOptions={{ color: '#fff', weight: 2, fillColor: SEV[a.live.severity] ?? '#58a6ff', fillOpacity: 1 }}>
                <Tooltip permanent direction="top" offset={[0, -8]}>{a.unit_id} · ETA {fmtEta(a.live.eta_seconds)}</Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        </section>

        <aside className="hd-side">
          <nav className="hd-tabs">
            <button className={tab === 'fleet' ? 'active' : ''} onClick={() => openTab('fleet')}>Fleet</button>
            <button className={tab === 'alerts' ? 'active' : ''} onClick={() => openTab('alerts')}>
              Alerts{unread > 0 && <b className="hd-badge">{unread}</b>}
            </button>
            <button className={tab === 'history' ? 'active' : ''} onClick={() => openTab('history')}>History</button>
            <button className={tab === 'capacity' ? 'active' : ''} onClick={() => openTab('capacity')}>Capacity</button>
          </nav>

          <div className="hd-list">
            {tab === 'fleet' && (<>
              {data && ambs.length === 0 && (
                <div className="hd-empty">
                  No ambulances yet. Ask crews to choose <b>Join as ambulance</b> and enter code <code>{data.org.join_code}</code>.
                </div>
              )}
              {ambs.map((a) => {
                const al = lastAlert[a.unit_id]
                return (
                  <div key={a.id} className={`hd-card ${a.online ? 'hd-card--on' : ''}`}
                    onClick={() => a.live && setFocus([a.live.lat, a.live.lng])}>
                    <div className="hd-card__top">
                      <strong>{a.unit_id}</strong>
                      <span className={`hd-pill ${a.online ? 'on' : 'off'}`}>{a.online ? '● Live' : 'Offline'}</span>
                    </div>
                    <div className="hd-card__sub">{a.display_name !== a.unit_id && `${a.display_name} · `}{a.email}</div>
                    {a.live && (
                      <dl>
                        <div><dt>Status</dt><dd>{a.live.state}</dd></div>
                        <div><dt>ETA</dt><dd>{fmtEta(a.live.eta_seconds)}</dd></div>
                        <div><dt>Distance</dt><dd>{fmtDist(a.live.distance_remaining_m)}</dd></div>
                        <div><dt>Speed</dt><dd>{Math.round(a.live.speed_kmh)} km/h</dd></div>
                        <div><dt>Severity</dt><dd style={{ color: SEV[a.live.severity] }}>{a.live.severity}</dd></div>
                        <div><dt>Emergency</dt><dd>{a.live.emergency_type ?? '—'}</dd></div>
                        <div className="wide"><dt>Road</dt><dd>{a.live.current_road_name || '—'}</dd></div>
                      </dl>
                    )}
                    {a.online && al && (
                      <div className="hd-patient">
                        <b>Patient</b> · blood {al.blood_type}
                        <Vitals v={al.vitals} />
                      </div>
                    )}
                    <button className="hd-remove" onClick={(e) => { e.stopPropagation(); remove(a) }}>Remove</button>
                  </div>
                )
              })}
            </>)}

            {tab === 'alerts' && (<>
              {alerts.length === 0 && <div className="hd-empty">No alerts yet. Pre-alerts appear here when an ambulance is 2 km from its hospital.</div>}
              {alerts.map((e, i) => {
                const a = e.alert, arrival = a.event === 'arrival'
                return (
                  <div key={i} className={`hd-card hd-alert ${arrival ? '' : 'hd-alert--hot'}`}>
                    <div className="hd-card__top">
                      <strong>{arrival ? '✓ Arrived' : '🚨 Pre-alert'} · {e.unit_id}</strong>
                      <span className="hd-time">{fmtTime(e.ts)}</span>
                    </div>
                    <div className="hd-card__sub">→ {a.hospital_name}</div>
                    {!arrival && (<>
                      <dl>
                        <div><dt>Emergency</dt><dd>{a.emergency_type}</dd></div>
                        <div><dt>Severity</dt><dd style={{ color: SEV[a.severity] }}>{a.severity}</dd></div>
                        <div><dt>ETA</dt><dd>{fmtEta(a.eta_s)}</dd></div>
                        <div><dt>Blood type</dt><dd>{a.blood_type}</dd></div>
                      </dl>
                      <Vitals v={a.vitals} />
                      {a.vitals?.resources_needed?.length > 0 && (
                        <div className="hd-res">Prepare: {a.vitals.resources_needed.join(', ')}</div>
                      )}
                      {a.notes && <div className="hd-note">“{a.notes}”</div>}
                    </>)}
                  </div>
                )
              })}
            </>)}

            {tab === 'capacity' && <CapacityForm token={token} />}

            {tab === 'history' && (<>
              {!history && <div className="hd-empty">Loading…</div>}
              {history && (<>
                <div className="hd-stats">
                  <div><b>{history.summary.n}</b><span>trips</span></div>
                  <div><b>{history.summary.done ?? 0}</b><span>completed</span></div>
                  <div><b>{history.summary.avg_duration_s ? fmtEta(history.summary.avg_duration_s) : '—'}</b><span>avg time</span></div>
                  <div><b>{fmtEta(history.summary.signal_saved_s ?? 0)}</b><span>saved at signals</span></div>
                </div>
                {history.trips.length === 0 && <div className="hd-empty">No trips yet. Completed and cancelled runs are recorded here.</div>}
                {history.trips.map((t) => (
                  <div key={t.id} className="hd-card">
                    <div className="hd-card__top">
                      <strong>{t.unit_id}</strong>
                      <span className={`hd-pill ${t.status === 'completed' ? 'on' : 'off'}`}>{t.status}</span>
                    </div>
                    <div className="hd-card__sub">{fmtDate(t.started_at)} · {t.emergency_type ?? '—'} → {t.hospital_name ?? '—'}</div>
                    {t.status !== 'active' && (
                      <dl>
                        <div><dt>Travel time</dt><dd>{fmtEta(t.duration_s)}</dd></div>
                        <div><dt>Distance</dt><dd>{fmtDist(t.distance_m)}</dd></div>
                        <div><dt>Signals cleared</dt><dd>{t.preemptions ?? 0} ({fmtEta(t.signal_saved_s ?? 0)} saved)</dd></div>
                        <div><dt>Reroutes</dt><dd>{t.reroute_count ?? 0}</dd></div>
                      </dl>
                    )}
                  </div>
                ))}
              </>)}
            </>)}
          </div>
        </aside>
      </main>
    </div>
  )
}
