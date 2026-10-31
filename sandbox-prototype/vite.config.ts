import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': process.env.API_PROXY_TARGET || 'http://127.0.0.1:18080',
    },
  },
})
