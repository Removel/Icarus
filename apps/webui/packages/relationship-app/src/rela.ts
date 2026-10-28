export class RelaError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = '',
    public uncertain = false,
  ) {
    super(message);
  }
}

export const pathPart = (value: string) => encodeURIComponent(value);
export function queryPath(
  path: string,
  values: Record<string, string | boolean | number | undefined>,
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== undefined && value !== '') query.set(key, String(value));
  return path + (query.size ? '?' + query : '');
}

export async function request<T>(
  path: string,
  options: { signal?: AbortSignal; method?: string; body?: unknown } = {},
): Promise<T> {
  const write = Boolean(options.method && options.method !== 'GET');
  const timeout = AbortSignal.timeout(15000);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  let response: Response;
  try {
    response = await fetch('/api/rela' + path, {
      method: options.method || 'GET',
      signal,
      headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new RelaError(
      write
        ? '请求中断，写入结果待核对。请先刷新确认，再决定是否重新提交。'
        : timeout.aborted
          ? '人物服务请求超时，请重试。'
          : '人物服务不可用，请检查服务连接。',
      0,
      '',
      write,
    );
  }
  if (!response.ok) {
    const value = await response.json().catch(() => null);
    const code = value?.error?.code || '';
    const hint =
      response.status === 401 || response.status === 403
        ? '人物服务鉴权失败，请检查服务端配置。'
        : response.status === 404
          ? '人物或相关记录不存在，可能已归档。'
          : response.status === 409
            ? '数据存在冲突，请核对最新记录后修改。'
            : code === 'VALIDATION_ERROR'
              ? '请求参数或游标无效，请检查输入或回到首屏。'
              : response.status >= 500
                ? '人物服务暂时不可用，请重试。'
                : '操作失败，请检查输入。';
    throw new RelaError(hint, response.status, code, write && response.status >= 500);
  }
  if (response.status === 204) return undefined as T;
  try {
    return await response.json();
  } catch {
    throw new RelaError(
      write ? '响应无法读取，写入结果待核对。请刷新确认。' : '人物服务返回了无法读取的数据。',
      response.status,
      '',
      write,
    );
  }
}

export const personPath = (id: string) => '/v1/persons/' + pathPart(id);
