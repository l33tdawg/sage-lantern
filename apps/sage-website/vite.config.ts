import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';
// Static export is served by the owner's existing Natter host.
export default defineConfig({
  css: { postcss: { plugins: [tailwindcss()] } },
  server: { host: '127.0.0.1', watch: { useFsEvents: false, usePolling: true } },
  plugins: [vinext()],
});
