import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import semiPlugin from '@douyinfe/semi-vite-plugin';

export default defineConfig({
  plugins: [react(), semiPlugin({ theme: '@semi-bot/semi-theme-universedesign' })],
  base: './',
});
