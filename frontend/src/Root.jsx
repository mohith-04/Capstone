/**
 * Root.jsx — picks which view to show
 *   no session            -> LoginPage (or the original demo, no account)
 *   role = ambulance      -> App (the existing ambulance view), tagged with its unit ID
 *   role = hospital       -> HospitalDashboard
 */
import { useState, useCallback } from 'react'
import App from './App.jsx'
import LoginPage from './pages/LoginPage.jsx'
import HospitalDashboard from './pages/HospitalDashboard.jsx'
import { loadSession, saveSession, clearSession } from './session'

export default function Root() {
  const [session, setSession] = useState(loadSession)
  const [demo, setDemo] = useState(false)

  const onSession = (s) => { saveSession(s); setSession(s) }
  const onLogout = useCallback(() => { clearSession(); setSession(null); setDemo(false) }, [])

  if (session?.user.role === 'hospital') return <HospitalDashboard session={session} onLogout={onLogout} />
  if (session?.user.role === 'ambulance') return <App ambulanceId={session.user.unit_id} user={session.user} onLogout={onLogout} />
  if (demo) return <App onLogout={onLogout} />
  return <LoginPage onSession={onSession} onDemo={() => setDemo(true)} />
}
