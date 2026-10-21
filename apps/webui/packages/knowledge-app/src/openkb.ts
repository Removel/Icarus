import type { KnowledgeBase, Source, WikiPage } from './types';
import type { GraphData } from './evidence';

const origin = (
  (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env
    .VITE_OPENKB_API_ORIGIN ?? ''
).replace(/\/$/, '');
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请重试。';
async function request<T>(
  path: string,
  body?: object | FormData,
  signal?: AbortSignal,
  method?: string,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(origin + '/api/v1/' + path, {
      method: method ?? (body ? 'POST' : 'GET'),
      signal,
      headers:
        body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('无法连接知识服务，请检查连接后重试。');
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403)
      throw new Error('无权访问知识服务，请检查登录状态或服务授权。');
    if (response.status === 404) throw new Error('内容不存在或已被移除，请刷新列表。');
    if (response.status === 409) throw new Error('资料标识存在歧义或正在处理，请刷新后重试。');
    throw new Error('知识服务请求失败（' + response.status + '），请稍后重试。');
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error('知识服务返回了无法读取的数据，请检查服务地址。');
  }
}
type ContentList = {
  documents: {
    hash: string;
    name: string;
    doc_name?: string | null;
    source_path?: string | null;
    display_type: string;
    pages: number | null;
  }[];
  summaries: string[];
  concepts: string[];
  entities: string[];
};
export async function listBases(signal?: AbortSignal): Promise<string[]> {
  const result = await request<{ knowledge_bases: { name: string }[] }>('kbs', undefined, signal);
  return result.knowledge_bases.map((item) => item.name);
}
export function pageBody(content: string) {
  return content.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '');
}
export async function loadBase(
  name: string,
  signal?: AbortSignal,
): Promise<{ base: KnowledgeBase; graph: GraphData; graphError: string }> {
  const [listResult, graphResult] = await Promise.allSettled([
    request<ContentList>('list', { kb: name }, signal),
    request<GraphData>('graph', { kb: name }, signal),
  ]);
  if (listResult.status === 'rejected') throw listResult.reason;
  const list = listResult.value;
  const graph =
    graphResult.status === 'fulfilled' ? graphResult.value : { nodes: [], edges: [], types: [] };
  const graphError = graphResult.status === 'rejected' ? errorMessage(graphResult.reason) : '';
  const pages: WikiPage[] = (['summaries', 'concepts', 'entities'] as const).flatMap((kind) =>
    list[kind].map((stem) => {
      const path = kind + '/' + stem;
      const node = graph.nodes.find((item) => item.id === path);
      return {
        path,
        kind,
        title: node?.label ?? stem,
        summary: node?.description ?? '',
        content: '',
        sources: node?.sources ?? [],
        readState: 'unloaded' as const,
      };
    }),
  );
  const documents: Source[] = list.documents.map((item) => ({
    hash: item.hash,
    name: item.name,
    display_type: item.display_type,
    pages: item.pages,
    docName: item.doc_name ?? undefined,
    sourcePath: item.source_path ?? undefined,
    readState: 'unloaded',
    content: '',
  }));
  return { base: { name, description: '', documents, pages }, graph, graphError };
}
export async function loadPage(kb: string, path: string, signal?: AbortSignal) {
  const result = await request<{ content: string }>('page', { kb, path }, signal);
  return { content: pageBody(result.content) };
}
export const loadSource = (kb: string, hash: string, signal?: AbortSignal) =>
  request<{ content: string; doc_name: string }>('document/source', { kb, hash }, signal);
export async function savePage(kb: string, path: string, content: string) {
  const result = await request<{
    status: string;
    content: string | null;
    ghosts_stripped: string[];
  }>('page', { kb, path, content }, undefined, 'PUT');
  if (result.status !== 'saved') throw new Error('页面未保存，请刷新后重试。');
  return result;
}
export const createBase = (kb: string) => request<{ kb: string; created: boolean }>('init', { kb });
export type ImportResult = {
  added_count: number;
  skipped_count: number;
  failed_count: number;
  files: { original_name: string; status: string; message: string }[];
};
export function importFiles(kb: string, files: File[]) {
  const form = new FormData();
  form.set('kb', kb);
  form.set('stream', 'false');
  files.forEach((file) => form.append('files', file));
  return request<ImportResult>('add', form);
}
export async function recompile(kb: string, source: Source) {
  const result = await request<{
    status: string;
    recompiled: number;
    skipped: number;
    docs: { status: string; message?: string }[];
  }>('recompile', { kb, doc_name: source.hash, stream: false });
  if (result.status !== 'done' || result.docs.some((item) => item.status === 'error'))
    throw new Error('编译未全部完成，请检查资料与服务状态后重试。');
  return result;
}
export type RemoveResult = {
  status: string;
  actions: { tag: string; target: string }[];
  message?: string;
  pageindex_error?: string;
};
export const removeSource = (kb: string, source: Source, dryRun: boolean, signal?: AbortSignal) =>
  request<RemoveResult>(
    'remove',
    { kb, identifier: source.hash, dry_run: dryRun, stream: false },
    signal,
  );
export type LintReport = {
  skipped: boolean;
  reason?: string;
  message: string;
  structural_report?: string;
  knowledge_report?: string;
};
export const lint = (kb: string, signal?: AbortSignal) =>
  request<LintReport>('lint', { kb, fix: false }, signal);

export async function listReports(kb: string, signal?: AbortSignal): Promise<string[]> {
  const result = await request<{ reports: string[] }>('list', { kb }, signal);
  return (result.reports ?? [])
    .filter((name) => name.endsWith('.md') && !/[\\/]/.test(name) && !name.startsWith('.'))
    .sort()
    .reverse();
}
export const deleteReport = (kb: string, name: string) =>
  request<{ status: string }>('report/delete', { kb, path: 'reports/' + name });
