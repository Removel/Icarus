import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import semiPlugin from '@douyinfe/semi-vite-plugin';

export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url));
  const env = loadEnv(mode, repoRoot, 'ICARUS_');
  const token = env.ICARUS_OPENKB_API_TOKEN;
  return {
    plugins: [react(), semiPlugin({ theme: '@semi-bot/semi-theme-universedesign' })],
    base: './',
    server: {
      proxy: {
        '/api/mem0': {
          target: env.ICARUS_MEM0_ENDPOINT || 'http://127.0.0.1:8888',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/mem0/, ''),
          headers: env.ICARUS_MEM0_API_KEY ? { 'X-API-Key': env.ICARUS_MEM0_API_KEY } : undefined,
        },
        '/rpc': {
          target: env.ICARUS_GATEWAY_ENDPOINT || 'http://127.0.0.1:8765',
          ws: true,
        },
        '/api/v1': {
          target: env.ICARUS_OPENKB_ENDPOINT || 'http://127.0.0.1:7566',
          changeOrigin: true,
          // This header is added by Vite in Node, never included in browser code.
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        },
      },
    },
  };
});
