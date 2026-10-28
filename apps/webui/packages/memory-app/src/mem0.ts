import { memoryDate, type MemoryEntry } from './types';

export const pageSize = 15;
export type MemoryPage = {
  results: MemoryEntry[];
  page: number;
  page_size: number;
  total: number;
  counts: { all: number; active: number; expired: number };
  categories: string[];
  users: string[];
  scopes: string[];
  truncated: boolean;
};
export const message = (error: unknown) =>
  error instanceof Error ? error.message : '记忆服务请求失败。';

async function request<T>(
  path: string,
  method = 'GET',
  body?: object,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/mem0' + path, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('无法连接记忆服务，请检查连接后重试。');
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403)
      throw new Error('无权访问记忆服务，请检查入口登录或服务授权。');
    if (response.status === 404) throw new Error('这条记忆不存在或已被移除。');
    throw new Error(`记忆服务请求失败（${response.status}），请重试。`);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error('记忆服务返回了无法读取的数据。');
  }
}

function record(value: unknown): MemoryEntry {
  if (
    !value ||
    typeof value !== 'object' ||
    !('id' in value) ||
    typeof value.id !== 'string' ||
    !('memory' in value) ||
    typeof value.memory !== 'string'
  )
    throw new Error('记忆服务返回了无效的记忆记录。');
  const row = value as Record<string, unknown>;
  const metadata =
    row.metadata && typeof row.metadata === 'object'
      ? (row.metadata as Record<string, unknown>)
      : {};
  return {
    id: value.id,
    memory: value.memory,
    user_id: String(row.user_id ?? ''),
    agent_id: String(row.agent_id ?? ''),
    run_id: String(row.run_id ?? ''),
    expiration_date: typeof row.expiration_date === 'string' ? row.expiration_date : null,
    created_at: String(row.created_at ?? ''),
    updated_at: String(row.updated_at ?? row.created_at ?? ''),
    metadata: { ...metadata, category: String(metadata.category || '未分类') },
    history: [],
  };
}

export async function list(query: string, signal?: AbortSignal): Promise<MemoryPage> {
  const result = await request<Omit<MemoryPage, 'results'> & { results: unknown[] }>(
    `/memories/page?${query}`,
    'GET',
    undefined,
    signal,
  );
  if (
    !Array.isArray(result.results) ||
    !result.counts ||
    !Number.isInteger(result.total) ||
    !Number.isInteger(result.page_size) ||
    result.page_size < 1 ||
    result.page_size > 100 ||
    !Array.isArray(result.categories) ||
    !Array.isArray(result.users) ||
    !Array.isArray(result.scopes)
  )
    throw new Error('记忆服务返回了无效的分页列表，请更新记忆服务。');
  return { ...result, results: result.results.map(record) };
}

export async function get(id: string, signal?: AbortSignal) {
  const value = await request<unknown>(
    '/memories/' + encodeURIComponent(id),
    'GET',
    undefined,
    signal,
  );
  return value === null ? null : record(value);
}

export async function history(id: string, signal?: AbortSignal): Promise<MemoryEntry['history']> {
  const value = await request<unknown>(
    '/memories/' + encodeURIComponent(id) + '/history',
    'GET',
    undefined,
    signal,
  );
  const rows = Array.isArray(value)
    ? value
    : value && typeof value === 'object' && 'results' in value
      ? value.results
      : null;
  if (!Array.isArray(rows)) throw new Error('记忆历史格式不可读取。');
  return rows
    .map((row) => {
      const labels: string[] = [];
      const event = String(row.event ?? '').toUpperCase();
      const before = typeof row.old_memory === 'string' ? row.old_memory : undefined;
      const after = typeof row.new_memory === 'string' ? row.new_memory : undefined;
      if (event === 'ADD') labels.push('创建记忆');
      else if (event === 'DELETE') labels.push('删除记忆');
      else if (before !== undefined && after !== before) labels.push('修正内容');
      const changes = row.changes;
      const details: string[] = [];
      if (changes && typeof changes === 'object') {
        const expiry = changes.expiration_date;
        if (expiry && typeof expiry === 'object') {
          labels.push(
            expiry.after === '1970-01-01'
              ? '暂停使用'
              : expiry.after === null &&
                  typeof expiry.before === 'string' &&
                  expiry.before < new Date().toLocaleDateString('en-CA')
                ? '恢复使用'
                : '修改有效期',
          );
          const date = (value: unknown) =>
            value === '1970-01-01' ? '已停用' : typeof value === 'string' ? value : '长期有效';
          details.push(`有效期：${date(expiry.before)} → ${date(expiry.after)}`);
        }
        const category = changes.category;
        if (category && typeof category === 'object') {
          labels.push('修改分类');
          details.push(
            `分类：${String(category.before ?? '未分类')} → ${String(category.after ?? '未分类')}`,
          );
        }
      }
      return {
        date: memoryDate(String(row.updated_at ?? row.created_at ?? '')),
        label: labels.join('、') || '更新了记忆',
        content: after,
        previous: before,
        details,
      };
    })
    .reverse();
}

export async function create(
  item: Pick<
    MemoryEntry,
    'memory' | 'user_id' | 'agent_id' | 'run_id' | 'metadata' | 'expiration_date'
  >,
) {
  const { memory, ...fields } = item;
  const value = await request<{ results: { id: string }[] }>('/memories', 'POST', {
    ...fields,
    messages: [{ role: 'user', content: memory }],
    infer: false,
  });
  const id = value.results?.[0]?.id;
  if (!id) throw new Error('服务未返回新记忆标识，请刷新列表确认结果后再操作。');
  return id;
}

export const update = (
  id: string,
  fields: { text?: string; metadata?: MemoryEntry['metadata']; expiration_date?: string | null },
) => request('/memories/' + encodeURIComponent(id), 'PUT', fields);
export const remove = (id: string) => request('/memories/' + encodeURIComponent(id), 'DELETE');
