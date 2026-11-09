/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

const api = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': { target: api, changeOrigin: false },
      '/mcp': { target: api, changeOrigin: false },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { include: ['src/**/*.test.ts'], environment: 'node', setupFiles: ['src/test-setup.ts'] },
});
