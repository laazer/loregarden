import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The server this client talks to. The main server by default; a client
// launched from the Local instances panel is handed a branch server's URL here
// (services/local_instances.py), which is how a UI change runs against a
// server change without either touching main.
const apiTarget = (process.env.LOREGARDEN_API_TARGET || 'http://127.0.0.1:8000').replace(/\/$/, '')
const socketTarget = apiTarget.replace(/^http/, 'ws')

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Loopback unless asked otherwise, matching scripts/dev-server.sh. Set
    // LOREGARDEN_DEV_HOST=0.0.0.0 to serve the local network.
    host: process.env.LOREGARDEN_DEV_HOST || undefined,
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
      // The sockets too, so a browser that is not on this machine can reach the
      // whole app through one origin. Serving the API on a second origin works
      // over loopback but not always over the network: a page fetching a LAN
      // address cross-origin is blocked outright by some browsers, before CORS
      // is ever consulted.
      '/ws': { target: socketTarget, ws: true },
      '/terminal': { target: socketTarget, ws: true },
    },
  },
})
