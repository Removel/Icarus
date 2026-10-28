import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import semiPlugin from '@douyinfe/semi-vite-plugin';
import precompress from './precompress';

export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url));
  const env = loadEnv(mode, repoRoot, 'ICARUS_');
  const token = env.ICARUS_OPENKB_API_TOKEN;
  const relaToken =
    process.env.RELA_MEM_API_TOKEN || loadEnv(mode, repoRoot, 'RELA_MEM_').RELA_MEM_API_TOKEN;
  return {
    plugins: [react(), semiPlugin({ theme: '@semi-bot/semi-theme-universedesign' }), precompress()],
    base: './',
    server: {
      proxy: {
        '^/api/rela(?:/|\\?|$)': {
          target: env.ICARUS_RELATIONSHIP_ENDPOINT || 'http://127.0.0.1:8767',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/rela/, '') || '/',
          configure: (proxy) => {
            proxy.on('proxyReq', (request) => {
              for (const header of ['authorization', 'cookie', 'x-api-key', 'proxy-authorization'])
                request.removeHeader(header);
              if (relaToken) request.setHeader('Authorization', `Bearer ${relaToken}`);
            });
          },
        },
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
        '/resources': {
          target: env.ICARUS_GATEWAY_ENDPOINT || 'http://127.0.0.1:8765',
          changeOrigin: true,
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
