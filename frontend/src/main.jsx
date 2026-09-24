/**
 * main.jsx — React app entry point
 *
 * Imports Leaflet's CSS here (at the top level) so it is bundled once
 * and available globally before any map component mounts. Leaflet requires
 * its CSS to be loaded before the map renders, otherwise icons and tiles
 * display incorrectly.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'leaflet/dist/leaflet.css'   // ← must come before any Leaflet component
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
