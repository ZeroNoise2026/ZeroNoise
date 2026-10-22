import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // One .env for the whole monorepo, at the repo root. Vite only exposes
  // VITE_* keys to the browser bundle.
  envDir: '../..',
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
