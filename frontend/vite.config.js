/**
 * vite.config.js
 *
 * The proxy entry below is the key Stage 1 wiring:
 * any request the React app makes to /api/* is forwarded
 * to the FastAPI backend on port 8000. This avoids CORS issues
 * during development without needing to touch the browser or
 * the backend's CORS settings every time.
 *
 * In production, nginx (or a similar reverse proxy) would do
 * the same job at the infrastructure level.
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // All calls to /api/** are forwarded to the FastAPI backend.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
