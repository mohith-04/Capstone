/**
 * Toast.jsx — Slide-in notification system
 * =========================================
 *
 * Used to surface alert events (pre-alert sent, ETA update, arrived) and the
 * mission-complete banner without forcing the user to switch tabs.
 *
 * Design decisions:
 *   - Portal-based: toasts render outside the sidebar DOM tree so they float
 *     over the map.  We use a simple portal-like approach via a fixed container.
 *   - Auto-dismiss: toasts disappear after 5s (alert events) or 8s (mission
 *     complete) so they don't obscure the map indefinitely.
 *   - The container sits at top-right, left of the sidebar, so it doesn't cover
 *     the ambulance while it's in the Vijayawada city area (centre-left of map).
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import './Toast.css'

const TOAST_ICONS = {
  initial: '🔵',
  update:  '🟡',
  arrival: '🟢',
  arrived: '🏁',
}

const TOAST_TITLES = {
  initial: 'Pre-Alert Sent',
  update:  'ETA Update',
  arrival: 'Ambulance Arrived',
  arrived: 'Mission Complete',
}

// ── Single toast item ─────────────────────────────────────────────────────────

function ToastItem({ toast, onDismiss }) {
  const [exiting, setExiting] = useState(false)

  useEffect(() => {
    const duration = toast.type === 'arrived' ? 8000 : 5000
    const timer = setTimeout(() => {
      setExiting(true)
      // Remove from DOM after animation completes
      setTimeout(() => onDismiss(toast.id), 260)
    }, duration)
    return () => clearTimeout(timer)
  }, [toast.id, toast.type, onDismiss])

  return (
    <div className={`toast toast--${toast.type} ${exiting ? 'toast--exiting' : ''}`}>
      <span className="toast__icon">{TOAST_ICONS[toast.type]}</span>
      <div className="toast__body">
        <div className="toast__title">{TOAST_TITLES[toast.type]}</div>
        <div className="toast__text">{toast.text}</div>
        {toast.sub && <div className="toast__sub">{toast.sub}</div>}
      </div>
    </div>
  )
}

// ── Toast container ───────────────────────────────────────────────────────────

/**
 * @param {{ toasts: Array, onDismiss: function }} props
 */
export function ToastContainer({ toasts, onDismiss }) {
  if (toasts.length === 0) return null
  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

// ── useToasts hook ────────────────────────────────────────────────────────────

let _nextId = 1

/**
 * Returns { toasts, addToast, dismissToast }.
 * addToast({ type, text, sub }) → toast id
 */
export function useToasts() {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback(({ type, text, sub }) => {
    const id = _nextId++
    setToasts((prev) => [...prev, { id, type, text, sub }])
    return id
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return { toasts, addToast, dismissToast }
}
