import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    outDir: 'dist-prototype-v1',
    rollupOptions: {
      input: fileURLToPath(new URL('./prototype-v1.html', import.meta.url)),
    },
  },
})
