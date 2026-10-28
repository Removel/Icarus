import http from 'node:http';
import https from 'node:https';
import { createReadStream } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, relative, extname, isAbsolute } from 'node:path';
import { timingSafeEqual } from 'node:crypto';

const env = process.env;
const host = env.ICARUS_WEBUI_HOST || '127.0.0.1';
const port = Number(env.ICARUS_WEBUI_PORT || 8080);
const user = env.ICARUS_WEBUI_USER || '';
const password = env.ICARUS_WEBUI_PASSWORD || '';
if (Boolean(user) !== Boolean(password) || user.includes(':'))
  throw new Error('Set a valid WEBUI user and password together.');
if (!['127.0.0.1', '::1', 'localhost'].includes(host) && !password)
  throw new Error('Non-loopback listening requires ICARUS_WEBUI_USER and ICARUS_WEBUI_PASSWORD.');
const origin = new URL(
  env.ICARUS_WEBUI_ORIGIN || `http://${host.includes(':') ? `[${host}]` : host}:${port}`,
).origin;
const root = await realpath(fileURLToPath(new URL('../apps/shell/dist/', import.meta.url)));
const routes = [
  {
    prefix: '/api/mem0',
    target: env.ICARUS_MEM0_ENDPOINT || 'http://127.0.0.1:8888',
    strip: true,
    headers: env.ICARUS_MEM0_API_KEY ? { 'x-api-key': env.ICARUS_MEM0_API_KEY } : {},
  },
  {
    prefix: '/api/v1',
    target: env.ICARUS_OPENKB_ENDPOINT || 'http://127.0.0.1:7566',
    headers: env.ICARUS_OPENKB_API_TOKEN
      ? { authorization: `Bearer ${env.ICARUS_OPENKB_API_TOKEN}` }
      : {},
  },
  { prefix: '/rpc', target: env.ICARUS_GATEWAY_ENDPOINT || 'http://127.0.0.1:8765', headers: {} },
].map((route) => ({ ...route, target: new URL(route.target) }));
for (const route of routes)
  if (
    !['http:', 'https:'].includes(route.target.protocol) ||
    route.target.pathname !== '/' ||
    route.target.search ||
    route.target.username ||
    route.target.password
  )
    throw new Error('Backend endpoints must be HTTP(S) origins without credentials or paths.');
const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};
const matches = (path, prefix) =>
  path === prefix || path.startsWith(prefix + '/') || path.startsWith(prefix + '?');
function authorized(request) {
  if (!password) return true;
  const expected = Buffer.from('Basic ' + Buffer.from(`${user}:${password}`).toString('base64'));
  const actual = Buffer.from(request.headers.authorization || '');
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
function allowed(request, websocket = false) {
  if (request.headers.host !== new URL(origin).host) return false;
  if (request.headers['sec-fetch-site'] === 'cross-site') return false;
  if (!websocket && ['GET', 'HEAD'].includes(request.method)) return true;
  return request.headers.origin === origin;
}
function headers(request, route, websocket) {
  const result = { ...request.headers };
  const nominated = String(result.connection || '')
    .split(',')
    .map((name) => name.trim().toLowerCase());
  for (const key of [
    ...nominated,
    'authorization',
    'cookie',
    'x-api-key',
    'proxy-authorization',
    'forwarded',
    'origin',
    'connection',
    'upgrade',
    'keep-alive',
    'proxy-connection',
    'te',
    'trailer',
    'transfer-encoding',
  ])
    delete result[key];
  for (const key of Object.keys(result)) if (key.startsWith('x-forwarded-')) delete result[key];
  result.host = route.target.host;
  if (websocket) {
    result.connection = 'Upgrade';
    result.upgrade = 'websocket';
  }
  return { ...result, ...route.headers };
}
function upstream(request, route, websocket = false) {
  return (route.target.protocol === 'https:' ? https : http).request({
    hostname: route.target.hostname,
    port: route.target.port,
    protocol: route.target.protocol,
    method: request.method,
    path: route.strip ? request.url.slice(route.prefix.length) || '/' : request.url,
    headers: headers(request, route, websocket),
  });
}
function reply(response, code, message) {
  response.writeHead(code, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  response.end(message);
}
const server = http.createServer(async (request, response) => {
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Referrer-Policy', 'same-origin');
  response.setHeader('X-Frame-Options', 'DENY');
  if (request.url === '/health' && request.method === 'GET') return reply(response, 200, 'ok');
  if (!authorized(request)) {
    response.setHeader('WWW-Authenticate', 'Basic realm="Icarus", charset="UTF-8"');
    return reply(response, 401, 'Authentication required');
  }
  if (!allowed(request)) return reply(response, 403, 'Origin rejected');
  const route = routes.find((route) => matches(request.url, route.prefix));
  if (route) {
    if (route.prefix === '/rpc') return reply(response, 426, 'WebSocket required');
    const proxy = upstream(request, route);
    proxy.setTimeout(300000, () => proxy.destroy(new Error('timeout')));
    proxy.on('response', (incoming) => {
      const clean = { ...incoming.headers };
      for (const key of [
        'set-cookie',
        'www-authenticate',
        'connection',
        'transfer-encoding',
        'access-control-allow-origin',
        'access-control-allow-credentials',
      ])
        delete clean[key];
      clean['cache-control'] = 'no-store';
      response.writeHead(incoming.statusCode, clean);
      incoming.pipe(response);
      incoming.on('error', () => response.destroy());
    });
    proxy.on('error', () => {
      if (!response.headersSent) reply(response, 502, 'Backend unavailable');
      else response.destroy();
    });
    request.on('aborted', () => proxy.destroy());
    response.on('close', () => {
      if (!response.writableFinished) proxy.destroy();
    });
    request.pipe(proxy);
    return;
  }
  if (!['GET', 'HEAD'].includes(request.method)) return reply(response, 405, 'Method not allowed');
  try {
    const path = decodeURIComponent(new URL(request.url, origin).pathname);
    if (path.includes('\\') || path.includes('\0')) return reply(response, 400, 'Invalid path');
    const file = await realpath(resolve(root, '.' + (path === '/' ? '/index.html' : path)));
    const child = relative(root, file);
    if (child.startsWith('..') || isAbsolute(child) || !(await stat(file)).isFile())
      return reply(response, 404, 'Not found');
    response.writeHead(200, {
      'Content-Type': mime[extname(file)] || 'application/octet-stream',
      'Cache-Control': path.startsWith('/assets/')
        ? 'public, max-age=31536000, immutable'
        : 'no-cache',
    });
    if (request.method === 'HEAD') response.end();
    else {
      const stream = createReadStream(file);
      stream.on('error', () => response.destroy());
      stream.pipe(response);
    }
  } catch {
    reply(response, 404, 'Not found');
  }
});
server.on('upgrade', (request, socket, head) => {
  const reject = (code) => {
    socket.end(`HTTP/1.1 ${code}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
  };
  if (!authorized(request)) return reject('401 Unauthorized');
  if (!allowed(request, true)) return reject('403 Forbidden');
  if (request.url !== '/rpc' || request.headers.upgrade?.toLowerCase() !== 'websocket')
    return reject('404 Not Found');
  const proxy = upstream(request, routes[2], true);
  proxy.setTimeout(10000, () => proxy.destroy());
  proxy.on('upgrade', (incoming, peer, initial) => {
    peer.setTimeout(0);
    const handshake = [
      'HTTP/1.1 101 Switching Protocols',
      'Upgrade: websocket',
      'Connection: Upgrade',
    ];
    for (const name of [
      'sec-websocket-accept',
      'sec-websocket-protocol',
      'sec-websocket-extensions',
    ]) {
      if (incoming.headers[name]) handshake.push(`${name}: ${incoming.headers[name]}`);
    }
    socket.write(handshake.join('\r\n') + '\r\n\r\n');
    if (initial.length) socket.write(initial);
    if (head.length) peer.write(head);
    socket.pipe(peer);
    peer.pipe(socket);
    socket.on('error', () => peer.destroy());
    peer.on('error', () => socket.destroy());
    socket.on('close', () => peer.destroy());
    peer.on('close', () => socket.destroy());
  });
  proxy.on('response', (incoming) => {
    incoming.resume();
    reject('502 Bad Gateway');
  });
  proxy.on('error', () => {
    if (!socket.destroyed) reject('502 Bad Gateway');
  });
  socket.on('error', () => proxy.destroy());
  proxy.end();
});
server.listen(port, host, () => console.log(`Icarus WebUI listening on ${host}:${port}`));
const connections = new Set();
server.on('connection', (socket) => {
  connections.add(socket);
  socket.on('close', () => connections.delete(socket));
});
for (const signal of ['SIGINT', 'SIGTERM'])
  process.on(signal, () => {
    server.close();
    server.closeAllConnections();
    for (const socket of connections) socket.destroy();
  });
