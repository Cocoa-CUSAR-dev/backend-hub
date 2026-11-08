import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Kept apart from vite.config.ts so the production build config stays as is.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // api.ts and liffClient.ts read these at import time; real values come
    // from .env (see .env.sample), tests only need them to be set.
    env: {
      VITE_API_BASE_URL: 'https://chatbot.test',
      VITE_LIFF_ID: '1234567890-abcdefgh',
    },
  },
})
