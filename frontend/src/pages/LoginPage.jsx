import { useState } from 'react'
import { login, registerHospital, registerAmbulance } from '../session'
import './LoginPage.css'

const MODES = [
  { id: 'login',     label: 'Sign in' },
  { id: 'hospital',  label: 'Create hospital org' },
  { id: 'ambulance', label: 'Join as ambulance' },
]

export default function LoginPage({ onSession, onDemo }) {
  const [mode, setMode] = useState('login')
  const [f, setF] = useState({ email: '', password: '', org_name: '', join_code: '', unit_id: '', display_name: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      let session
      if (mode === 'login') session = await login({ email: f.email, password: f.password })
      else if (mode === 'hospital') session = await registerHospital({ org_name: f.org_name, email: f.email, password: f.password })
      else session = await registerAmbulance({ join_code: f.join_code, unit_id: f.unit_id, display_name: f.display_name, email: f.email, password: f.password })
      onSession(session)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>🚑 Ambulance Route Optimization</h1>
        <div className="login-tabs">
          {MODES.map((m) => (
            <button type="button" key={m.id}
              className={mode === m.id ? 'active' : ''}
              onClick={() => { setMode(m.id); setError('') }}>
              {m.label}
            </button>
          ))}
        </div>

        {mode === 'hospital' && (
          <label>Hospital / organisation name
            <input value={f.org_name} onChange={set('org_name')} required minLength={2} />
          </label>
        )}
        {mode === 'ambulance' && (<>
          <label>Join code (from your hospital)
            <input value={f.join_code} onChange={set('join_code')} required placeholder="A1B2C3" />
          </label>
          <label>Ambulance unit ID
            <input value={f.unit_id} onChange={set('unit_id')} required placeholder="AMB-101" pattern="[A-Za-z0-9_\-]+" />
          </label>
          <label>Crew / display name (optional)
            <input value={f.display_name} onChange={set('display_name')} />
          </label>
        </>)}

        <label>Email
          <input type="email" value={f.email} onChange={set('email')} required autoComplete="email" />
        </label>
        <label>Password {mode !== 'login' && <small>(min 8 characters)</small>}
          <input type="password" value={f.password} onChange={set('password')} required minLength={mode === 'login' ? 1 : 8}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
        </label>

        {error && <div className="login-error">{error}</div>}
        <button className="login-submit" disabled={busy}>{busy ? 'Please wait…' : MODES.find((m) => m.id === mode).label}</button>
        <button type="button" className="login-demo" onClick={onDemo}>Continue without an account (demo)</button>
      </form>
    </div>
  )
}
