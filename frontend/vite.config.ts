// path: frontend/vite.config.ts
import { defineConfig } from 'vite';
import { fileURLToPath, URL } from 'node:url';

const BACKEND = process.env.VITE_BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
  resolve: {
    alias: {
      // The protocol definition lives outside this package so the server and client can
      // share one file without publishing it.
      '@shared': fileURLToPath(new URL('../shared', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    fs: {
      // Required because @shared resolves above the Vite root.
      allow: ['..'],
    },
    proxy: {
      // Proxying in dev means the browser sees one origin, so there is no CORS setup and
      // no separate WebSocket host to configure.
      '/api': { target: BACKEND, changeOrigin: true },
      '/socket.io': { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  build: {
    target: 'es2020',
    sourcemap: true,
    chunkSizeWarningLimit: 900, // three.js alone is comfortably over the default
    rollupOptions: {
      output: {
        // Split the heavy vendors out so a gameplay change doesn't invalidate a ~700 kB
        // chunk the browser already has cached.
        manualChunks(id: string) {
          if (id.includes('node_modules/three')) return 'three';
          if (id.includes('node_modules/socket.io') || id.includes('node_modules/engine.io'))
            return 'net';
          return undefined;
        },
      },
    },
  },
});
