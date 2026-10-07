import { defineConfig } from 'vite'
import fs from 'node:fs'
const identity = JSON.parse(fs.readFileSync(new URL('../release_identity.json', import.meta.url), 'utf8'))
const marker = identity.version.replaceAll('.', '')

export default defineConfig({
  build: {
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-${marker}-[hash].js`,
        assetFileNames: `assets/[name]-${marker}-[hash][extname]`,
      },
    },
  },
  server: {
    host: '0.0.0.0',
    allowedHosts: ['terminal.local'],
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
