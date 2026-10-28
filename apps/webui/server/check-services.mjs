// Read-only checks against real service processes. Does not invoke a model or mutate data.
const env = process.env;
async function httpCheck(name, endpoint, path, headers, valid) {
  const response = await fetch(new URL(path, endpoint), {
    headers,
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  if (!valid(await response.json())) throw new Error(`${name}: unexpected response shape`);
  return name;
}
async function gatewayCheck() {
  const endpoint = new URL('/rpc', env.ICARUS_GATEWAY_ENDPOINT || 'http://127.0.0.1:8765');
  endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(endpoint);
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(new Error('Gateway: timeout')), 5000);
    const finish = (error) => {
      // Node 22 may emit another error synchronously from close() after a
      // failed handshake. Guard cleanup as well as Promise settlement.
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve('Gateway');
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)
        socket.close();
    };
    socket.addEventListener('open', () =>
      socket.send(
        JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'runtime.get_status', params: {} }),
      ),
    );
    socket.addEventListener('error', () => finish(new Error('Gateway: connection failed')));
    socket.addEventListener('close', () => finish(new Error('Gateway: connection closed')));
    socket.addEventListener('message', (event) => {
      try {
        const response = JSON.parse(String(event.data));
        if (response.id === 1)
          finish(
            response.result?.status === 'ready' ? null : new Error('Gateway: runtime not ready'),
          );
      } catch {
        finish(new Error('Gateway: invalid JSON-RPC'));
      }
    });
  });
}
const checks = [
  [
    'rela',
    () =>
      httpCheck(
        'rela',
        env.ICARUS_RELATIONSHIP_ENDPOINT || 'http://127.0.0.1:8767',
        '/v1/persons?limit=1',
        env.RELA_MEM_API_TOKEN ? { Authorization: `Bearer ${env.RELA_MEM_API_TOKEN}` } : {},
        (body) => Array.isArray(body.items),
      ),
  ],
  [
    'Mem0',
    () =>
      httpCheck(
        'Mem0',
        env.ICARUS_MEM0_ENDPOINT || 'http://127.0.0.1:8888',
        '/memories?show_expired=true&top_k=1',
        env.ICARUS_MEM0_API_KEY ? { 'X-API-Key': env.ICARUS_MEM0_API_KEY } : {},
        (body) => Array.isArray(body.results),
      ),
  ],
  [
    'OpenKB',
    () =>
      httpCheck(
        'OpenKB',
        env.ICARUS_OPENKB_ENDPOINT || 'http://127.0.0.1:7566',
        '/api/v1/kbs',
        env.ICARUS_OPENKB_API_TOKEN
          ? { Authorization: `Bearer ${env.ICARUS_OPENKB_API_TOKEN}` }
          : {},
        (body) => Array.isArray(body.knowledge_bases),
      ),
  ],
  ['Gateway', gatewayCheck],
];
const results = await Promise.allSettled(checks.map(([, check]) => check()));
for (const [index, result] of results.entries()) {
  if (result.status === 'fulfilled') console.log(`${checks[index][0]}: ready`);
  else {
    // Deliberately omit URLs, credentials, response bodies and nested error causes.
    console.error(
      `${checks[index][0]}: unavailable (${result.reason instanceof Error ? result.reason.message : 'request failed'})`,
    );
    process.exitCode = 1;
  }
}
