import path from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// The tracker is its own deployment: a static site at the root of wherever it
// is hosted, talking to a firewall server over VITE_API_BASE. So no `base` and
// no outDir pointing back into the Python package -- the assets are /assets/…,
// which is what Vercel and every other static host serve with nothing
// configured. The firewall CLI serves this same web/dist at /tracker when you
// happen to be running both on one machine.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // `npm run dev` with no VITE_API_BASE set: the wire is proxied to the
    // firewall process on this machine, so the dev server is same-origin in
    // the same way production is, cookies included. FIREWALL_ORIGIN moves it
    // when the server is on another port or another box.
    proxy: {
      '/api': {
        target: process.env.FIREWALL_ORIGIN || 'http://localhost:842',
        changeOrigin: true,
      },
    },
  },
})
