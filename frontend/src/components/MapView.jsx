/**
 * MapView.jsx — Live Leaflet map (Stage 7: polished)
 * ================================
 *
 * Renders the full-screen interactive map showing:
 *   - Dark CartoDB basemap
 *   - Blue route polyline — updates live on reroute
 *   - 🟢 Start marker (Benz Circle)
 *   - 🚑 Ambulance marker — moves with every telemetry ping
 *   - Coloured CircleMarkers for each signal intersection
 *   - 🏥 Hospital marker at the destination
 *   - Map legend overlay (bottom-left)
 *
 * Why react-leaflet instead of raw Leaflet or Google Maps?
 *   react-leaflet v5 wraps Leaflet in React components, so updating the
 *   route polyline or ambulance position is just a prop change — no
 *   imperative DOM mutations.  Google Maps would require a paid API key.
 *
 * CartoDB dark_all tile layer:
 *   Free, no API key, looks excellent on a dark UI.
 *   Attribution required by CARTO's terms of service.
 */

import { useEffect } from 'react'
import {
  MapContainer, TileLayer, Polyline,
  Marker, Popup, Tooltip, useMap,
} from 'react-leaflet'
import L from 'leaflet'
import { DEFAULT_START } from '../hooks/useSimulation'
import './MapView.css'

// ── Fix Leaflet's broken default icon path (common Vite + asset-hash issue) ──
delete L.Icon.Default.prototype._getIconUrl

// ── Map constants ─────────────────────────────────────────────────────────────

const DEFAULT_CENTER = [DEFAULT_START.lat, DEFAULT_START.lng]
const DEFAULT_ZOOM = 14

const SIGNAL_COLORS = {
  normal: '#8b949e',
  priority_pending: '#d29922',
  priority_active: '#3fb950',
  restoring: '#58a6ff',
}

// ── Traffic light arm colours from signal phase ──────────────────────────────
// Returns {n, s, e, w} for the four road arms of an intersection.
// Phase A = N-S corridor green; Phase B = E-W corridor green.
function getArmColors(phase, mode) {
  const G = '#3fb950'   // green
  const A = '#d29922'   // amber
  const R = '#c0392b'   // red (darker for contrast on light map)
  const DIM = '#555'    // dimmed in normal mode

  if (mode === 'normal') {
    // Normal cycling — show the current phase accurately but dimmed
    if (phase === 'green_A') return { n: G, s: G, e: DIM, w: DIM }
    if (phase === 'amber_A') return { n: A, s: A, e: DIM, w: DIM }
    if (phase === 'green_B') return { n: DIM, s: DIM, e: G, w: G }
    if (phase === 'amber_B') return { n: DIM, s: DIM, e: A, w: A }
    return { n: DIM, s: DIM, e: DIM, w: DIM }  // all_red
  }

  // Priority / restoring modes — fully saturated colours
  if (phase === 'green_A') return { n: G, s: G, e: R, w: R }
  if (phase === 'amber_A') return { n: A, s: A, e: R, w: R }
  if (phase === 'green_B') return { n: R, s: R, e: G, w: G }
  if (phase === 'amber_B') return { n: R, s: R, e: A, w: A }
  return { n: R, s: R, e: R, w: R }  // all_red
}

// Build a Leaflet DivIcon showing a "+" intersection with 4 coloured arms
function makeSignalIcon(phase, mode, isConflict) {
  const c = getArmColors(phase, mode)
  const border = isConflict
    ? '#f85149'
    : (mode === 'priority_active' ? '#3fb950'
      : mode === 'priority_pending' ? '#d29922'
      : '#555')
  const html = `
    <div style="position:relative;width:34px;height:34px;">
      <!-- N arm -->
      <div style="position:absolute;left:13px;top:0;width:8px;height:13px;
                  background:${c.n};border-radius:3px 3px 0 0;"></div>
      <!-- S arm -->
      <div style="position:absolute;left:13px;top:21px;width:8px;height:13px;
                  background:${c.s};border-radius:0 0 3px 3px;"></div>
      <!-- E arm -->
      <div style="position:absolute;top:13px;left:21px;width:13px;height:8px;
                  background:${c.e};border-radius:0 3px 3px 0;"></div>
      <!-- W arm -->
      <div style="position:absolute;top:13px;left:0;width:13px;height:8px;
                  background:${c.w};border-radius:3px 0 0 3px;"></div>
      <!-- Centre -->
      <div style="position:absolute;left:12px;top:12px;width:10px;height:10px;
                  background:#1c2128;border:2px solid ${border};border-radius:2px;
                  ${isConflict ? 'animation:conflict-blink 0.8s ease-in-out infinite;' : ''}
                  "></div>
    </div>
    <style>
      @keyframes conflict-blink {
        0%,100%{border-color:#f85149;} 50%{border-color:#ff8c00;}
      }
    </style>
  `
  return L.divIcon({
    className: '',
    html,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    popupAnchor: [0, -20],
  })
}

// ── DivIcon factories ─────────────────────────────────────────────────────────

const AMBULANCE_ICON = L.divIcon({
  className: '',
  html: `<div class="ambulance-icon">
    <div style="background: white; border: 2px solid #cf222e; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#cf222e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle><line x1="8" y1="9" x2="8" y2="9"></line></svg>
    </div>
  </div>`,
  iconSize: [36, 36],
  iconAnchor: [18, 18],
  popupAnchor: [0, -20],
})

// Orange ambulance icon for AMB-002 in dual mode
const AMBULANCE_ICON_2 = L.divIcon({
  className: '',
  html: `<div class="ambulance-icon">
    <div style="background: white; border: 2px solid #a371f7; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#a371f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle><line x1="8" y1="9" x2="8" y2="9"></line></svg>
    </div>
  </div>`,
  iconSize: [36, 36],
  iconAnchor: [18, 18],
  popupAnchor: [0, -20],
})

const HOSPITAL_ICON = L.divIcon({
  className: '',
  html: `<div class="hospital-icon">
    <div style="background: #2da44e; border: 2px solid white; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M19 3H5c-1.1 0-1.99.9-1.99 2L3 19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>
    </div>
  </div>`,
  iconSize: [32, 32],
  iconAnchor: [16, 16],
  popupAnchor: [0, -18],
})

const START_ICON = L.divIcon({
  className: '',
  html: `<div class="start-icon">
    <div style="background: #0969da; border: 2px solid white; border-radius: 50%; width: 20px; height: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.2);"></div>
  </div>`,
  iconSize: [20, 20],
  iconAnchor: [10, 10],
  popupAnchor: [0, -12],
})

// ── MapUpdater — smooth-follow ambulance position ─────────────────────────────
// Must be a child of MapContainer because useMap() only works inside it.

function MapUpdater({ position }) {
  const map = useMap()
  useEffect(() => {
    if (position) {
      map.setView(position, map.getZoom(), { animate: true, duration: 0.4 })
    }
  }, [position, map])
  return null
}

// ── Map Legend — rendered OUTSIDE MapContainer so z-index works ──────────────

function MapLegend() {
  return (
    <div className="map-legend">
      <div className="map-legend__title">Signal State</div>
      {[
        { color: '#8b949e', label: 'Normal' },
        { color: '#d29922', label: 'Priority Pending' },
        { color: '#3fb950', label: 'Priority Active' },
        { color: '#58a6ff', label: 'Restoring' },
        { color: '#f85149', label: 'Conflict (queued)' },
      ].map(({ color, label }) => (
        <div key={label} className="map-legend__item">
          <span className="map-legend__dot" style={{ background: color }} />
          {label}
        </div>
      ))}
      <div className="map-legend__divider" />
      {/* Signal cross icon explanation */}
      <div className="map-legend__item" style={{ alignItems: 'flex-start', gap: 6 }}>
        <div style={{ position: 'relative', width: 20, height: 20, flexShrink: 0, marginTop: 1 }}>
          <div style={{ position: 'absolute', left: 8, top: 0, width: 4, height: 7, background: '#3fb950', borderRadius: '2px 2px 0 0' }} />
          <div style={{ position: 'absolute', left: 8, top: 13, width: 4, height: 7, background: '#3fb950', borderRadius: '0 0 2px 2px' }} />
          <div style={{ position: 'absolute', top: 8, left: 13, width: 7, height: 4, background: '#c0392b', borderRadius: '0 2px 2px 0' }} />
          <div style={{ position: 'absolute', top: 8, left: 0, width: 7, height: 4, background: '#c0392b', borderRadius: '2px 0 0 2px' }} />
          <div style={{ position: 'absolute', left: 7, top: 7, width: 6, height: 6, background: '#1c2128', border: '1.5px solid #555', borderRadius: 1 }} />
        </div>
        <span style={{ fontSize: 10, lineHeight: 1.4 }}>
          <strong>+</strong> = intersection<br />
          green arm = active phase<br />
          red arm = stopped
        </span>
      </div>
      <div className="map-legend__divider" />
      <div className="map-legend__item">
        <div style={{width: 14, height: 14, borderRadius: '50%', background: '#0969da', border: '1px solid white'}}></div> Start
      </div>
      <div className="map-legend__item">
        <div style={{ background: '#2da44e', border: '1px solid white', borderRadius: '50%', width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="white"><path d="M19 3H5c-1.1 0-1.99.9-1.99 2L3 19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>
        </div> Hospital
      </div>
      <div className="map-legend__item">
        <div style={{ background: 'white', border: '2px solid #cf222e', borderRadius: '50%', width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#cf222e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle><line x1="8" y1="9" x2="8" y2="9"></line></svg>
        </div> AMB-001
      </div>
      <div className="map-legend__item">
        <div style={{ background: 'white', border: '2px solid #a371f7', borderRadius: '50%', width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#a371f7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle><line x1="8" y1="9" x2="8" y2="9"></line></svg>
        </div> AMB-002
      </div>
    </div>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtEta(s) {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

// ── Main component ────────────────────────────────────────────────────────────

/**
 * @param {{
 *   route: {coords: Array}|null,
 *   telemetry: object|null,
 *   signals: Array,
 *   hospital: object|null,
 *   simState: string,
 *   route2?: {coords: Array}|null,
 *   telemetry2?: object|null,
 * }} props
 */
export default function MapView({ route, telemetry, signals, hospital, simState,
                                  route2 = null, telemetry2 = null }) {
  const ambPosition  = telemetry  ? [telemetry.lat,  telemetry.lng]  : null
  const ambPosition2 = telemetry2 ? [telemetry2.lat, telemetry2.lng] : null
  const simActive = simState === 'running' || simState === 'arrived'

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <MapContainer
        center={DEFAULT_CENTER}
        zoom={DEFAULT_ZOOM}
        className="map-container"
        zoomControl
      >
        {/* Dark basemap — free, no API key */}
        <TileLayer
          // url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          url="https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=cb1_3tdu_1_c9bc1e107238646219491175"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
          subdomains="abcd"
          maxZoom={20}
        />

        {/* Smooth-follow ambulance */}
        {ambPosition && <MapUpdater position={ambPosition} />}

        {/* Route polyline
            OSMnx snaps end_lat/end_lng to the nearest drivable road node, so
            route.coords[-1] is that road node — not the hospital's exact location.
            We fix this visually by appending hospital.lat/lng as the final point,
            extending the polyline all the way to the hospital entrance.
            The last segment (road → hospital door) is drawn dashed to indicate
            it's an "off-network access road" rather than a routing decision. */}
        {route?.coords?.length > 1 && (() => {
          // Main road-network route (solid blue)
          const mainCoords = route.coords
          return (
            <>
              <Polyline
                positions={mainCoords}
                pathOptions={{ color: '#58a6ff', weight: 4, opacity: 0.85 }}
              />
              {/* Dashed connector: last road node → exact hospital coordinates */}
              {hospital && (
                <Polyline
                  positions={[
                    mainCoords[mainCoords.length - 1],
                    [hospital.lat, hospital.lng],
                  ]}
                  pathOptions={{
                    color:     '#58a6ff',
                    weight:    3,
                    opacity:   0.6,
                    dashArray: '8 6',
                  }}
                />
              )}
            </>
          )
        })()}

        {/* Start marker */}
        {simActive && (
          <Marker
            position={route?.coords?.[0] || [DEFAULT_START.lat, DEFAULT_START.lng]}
            icon={START_ICON}
          >
            <Popup>
              <div className="map-popup">
                <div className="map-popup__title">Start Point</div>
                <div className="map-popup__row">AMB-001 Start</div>
              </div>
            </Popup>
          </Marker>
        )}

        {/* Signal intersection markers — cross/+ shaped traffic light icons.
            Phase A (green_A) = N-S corridor green, E-W red.
            Phase B (green_B) = E-W corridor green, N-S red. */}
        {signals.map((s) => {
          const hasConflict = s.conflict_detected && s.pending_queue?.length > 0
          const icon = makeSignalIcon(s.phase, s.mode, hasConflict)
          return (
            <Marker key={s.node_id} position={[s.lat, s.lng]} icon={icon}>
              <Tooltip sticky>
                <div style={{ fontFamily: 'monospace', fontSize: 11 }}>
                  {hasConflict && (
                    <div style={{ color: '#f85149', fontWeight: 700, marginBottom: 3 }}>
                      ⚠ CONFLICT — {s.pending_queue.length + 1} ambulances queued
                    </div>
                  )}
                  <strong>{s.mode.replace(/_/g, ' ').toUpperCase()}</strong><br />
                  Phase: {s.phase}<br />
                  Serving: {s.priority_ambulance_id || '—'}<br />
                  {hasConflict && <>Waiting: {s.pending_queue.join(', ')}<br /></>}
                  {s.conflict_reason && (
                    <>Winner by: {s.conflict_reason}<br /></>
                  )}
                  Amb: {s.ambulance_direction} / Cross: {s.cross_direction}
                  {s.time_saved_s > 0 && <><br />Saved: {s.time_saved_s.toFixed(1)}s</>}
                </div>
              </Tooltip>
            </Marker>
          )
        })}

        {/* AMB-001 ambulance marker */}
        {ambPosition && (
          <Marker position={ambPosition} icon={AMBULANCE_ICON}>
            <Popup>
              <div className="map-popup">
                <div className="map-popup__title">AMB-001</div>
                <div className="map-popup__row">Speed <span>{Math.round(telemetry.speed_kmh)} km/h</span></div>
                <div className="map-popup__row">ETA <span>{fmtEta(telemetry.eta_seconds)}</span></div>
                <div className="map-popup__row">Road <span>{telemetry.current_road_name || 'Unknown'}</span></div>
                <div className="map-popup__row">Congestion <span>{telemetry.congestion_factor.toFixed(1)}×</span></div>
              </div>
            </Popup>
          </Marker>
        )}

        {/* AMB-002 route polyline — purple, only shown in dual mode */}
        {route2?.coords?.length > 1 && (() => {
          const mainCoords2 = route2.coords
          return (
            <>
              <Polyline
                positions={mainCoords2}
                pathOptions={{ color: '#a371f7', weight: 4, opacity: 0.85 }}
              />
              {hospital && (
                <Polyline
                  positions={[
                    mainCoords2[mainCoords2.length - 1],
                    [hospital.lat, hospital.lng],
                  ]}
                  pathOptions={{
                    color:     '#a371f7',
                    weight:    3,
                    opacity:   0.6,
                    dashArray: '8 6',
                  }}
                />
              )}
            </>
          )
        })()}

        {/* AMB-002 ambulance marker — purple icon */}
        {ambPosition2 && (
          <Marker position={ambPosition2} icon={AMBULANCE_ICON_2}>
            <Popup>
              <div className="map-popup">
                <div className="map-popup__title">AMB-002</div>
                <div className="map-popup__row">Speed <span>{Math.round(telemetry2.speed_kmh)} km/h</span></div>
                <div className="map-popup__row">ETA <span>{fmtEta(telemetry2.eta_seconds)}</span></div>
                <div className="map-popup__row">Road <span>{telemetry2.current_road_name || 'Unknown'}</span></div>
                <div className="map-popup__row">Congestion <span>{telemetry2.congestion_factor.toFixed(1)}×</span></div>
              </div>
            </Popup>
          </Marker>
        )}

        {/* Hospital marker */}
        {hospital && (
          <Marker position={[hospital.lat, hospital.lng]} icon={HOSPITAL_ICON}>
            <Popup>
              <div className="map-popup">
                <div className="map-popup__title">{hospital.short_name || hospital.name}</div>
                <div className="map-popup__row">Type <span>{hospital.type?.replace(/_/g, ' ')}</span></div>
                <div className="map-popup__row">Trauma bays <span>{hospital.trauma_bays}</span></div>
                <div className="map-popup__row">ICU beds <span>{hospital.icu_beds}</span></div>
                <div className="map-popup__row">Blood bank <span>{hospital.blood_bank ? '✓ Yes' : '✗ No'}</span></div>
                <div className="map-popup__row">Cath lab <span>{hospital.cath_lab ? '✓ Yes' : '✗ No'}</span></div>
              </div>
            </Popup>
          </Marker>
        )}
      </MapContainer>

      {/* Legend sits outside MapContainer so it's always on top */}
      <MapLegend />
    </div>
  )
}
