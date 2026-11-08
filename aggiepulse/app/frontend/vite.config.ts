import { defineConfig } from 'vite';
export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  // The LiveMap chunk is MapLibre (~1.1 MB) and is already lazy-loaded; the main chunk is ~260 kB.
  build: { chunkSizeWarningLimit: 1200 },
});
