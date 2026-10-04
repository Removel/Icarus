// Mirrors packages/gateway_protocol and apps/gateway/src/protocol/methods.py.
export type RuntimeUpdate = {
  workspace_key: string;
  session_id: string;
  task_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  sequence: number | null;
};
export type Session = {
  session_id: string;
  first_user_input: string;
  title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};
export type SessionStatus = {
  workspace_key: string;
  session_id: string;
  active_task_ids: string[];
};
export type History = {
  records: RuntimeUpdate[];
  next_after_sequence: number;
  has_more: boolean;
  history_cursor: number;
};
export class RpcError extends Error {}

export class Gateway {
  private socket: WebSocket;
  private sequence = 0;
  private pending = new Map<
    number,
    {
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  readonly ready: Promise<void>;
  constructor(onUpdate: (update: RuntimeUpdate) => void, onClose: () => void) {
    this.socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/rpc`,
    );
    this.ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error('连接 Agent 超时。'));
        this.socket.close();
      }, 10000);
      this.socket.addEventListener(
        'open',
        () => {
          clearTimeout(timer);
          resolve();
        },
        { once: true },
      );
      this.socket.addEventListener(
        'close',
        () => {
          clearTimeout(timer);
          reject(new Error('无法连接 Agent，请检查 Gateway 服务。'));
        },
        { once: true },
      );
    });
    this.socket.addEventListener('message', (event) => {
      try {
        const value = JSON.parse(String(event.data));
        if (value.jsonrpc !== '2.0') throw new Error('Invalid JSON-RPC');
        if (value.method === 'runtime.update') {
          const update = value.params;
          if (
            update &&
            typeof update.type === 'string' &&
            typeof update.session_id === 'string' &&
            typeof update.workspace_key === 'string' &&
            update.payload &&
            typeof update.payload === 'object'
          )
            onUpdate(update);
          return;
        }
        const request = this.pending.get(value.id);
        if (!request) return;
        this.pending.delete(value.id);
        clearTimeout(request.timer);
        if (value.error)
          request.reject(new RpcError(String(value.error.message ?? 'Agent 请求失败')));
        else request.resolve(value.result);
      } catch {
        this.socket.close(4002, 'Invalid gateway response');
      }
    });
    this.socket.addEventListener('close', () => {
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error('连接中断，操作结果尚未确认。'));
      }
      this.pending.clear();
      onClose();
    });
  }
  async request<T>(method: string, params: object = {}): Promise<T> {
    await this.ready;
    if (this.socket.readyState !== WebSocket.OPEN) throw new Error('Agent 连接已断开。');
    const id = ++this.sequence;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('Agent 响应超时，操作结果尚未确认。'));
      }, 30000);
      this.pending.set(id, { resolve: (value) => resolve(value as T), reject, timer });
      this.socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
    });
  }
  close() {
    this.socket.close();
  }
}
