import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Loopback unless asked otherwise, matching scripts/dev-server.sh. Set
    // LOREGARDEN_DEV_HOST=0.0.0.0 to serve the local network.
    host: process.env.LOREGARDEN_DEV_HOST || undefined,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      // The sockets too, so a browser that is not on this machine can reach the
      // whole app through one origin. Serving the API on a second origin works
      // over loopback but not always over the network: a page fetching a LAN
      // address cross-origin is blocked outright by some browsers, before CORS
      // is ever consulted.
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/terminal': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
