export type WebSource = {
  title: string;
  url: string;
  snippet?: string;
  siteName?: string;
  publishedAt?: string;
  provider?: string;
  canonicalUrl?: string;
  citeIndex: number;
};

export type OpenPage = { url: string; title: string; status: 'done'; contentLength?: number };
export type WebSearchState = {
  status: 'searching' | 'complete' | 'empty' | 'error' | 'stopped';
  total: number;
  queries: string[];
  sources: WebSource[];
  pages: OpenPage[];
  browsing: boolean;
  notice: string;
};
export type SearchPreference = { web_search: boolean; deep_search: boolean };
export type RequestOptions = SearchPreference & { local_time?: string; time_zone?: string };

export function localClock() {
  const now = new Date();
  const offset = -now.getTimezoneOffset();
  const pad = (value: number) => String(value).padStart(2, '0');
  const local = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  return {
    local_time: `${local}${offset >= 0 ? '+' : '-'}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`,
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

function httpUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

export function parseSearchState(payload: Record<string, unknown>): WebSearchState | undefined {
  const status = payload.status;
  if (
    status !== 'searching' &&
    status !== 'complete' &&
    status !== 'empty' &&
    status !== 'error' &&
    status !== 'stopped'
  )
    return;
  const sources: WebSource[] = [];
  for (const row of Array.isArray(payload.sources) ? payload.sources : []) {
    if (
      !row ||
      typeof row !== 'object' ||
      !httpUrl(row.url) ||
      typeof row.title !== 'string' ||
      !Number.isInteger(row.citeIndex) ||
      row.citeIndex < 1
    )
      continue;
    sources.push({
      url: row.url,
      title: row.title,
      citeIndex: row.citeIndex,
      snippet: typeof row.snippet === 'string' ? row.snippet : undefined,
      siteName: typeof row.siteName === 'string' ? row.siteName : undefined,
      publishedAt: typeof row.publishedAt === 'string' ? row.publishedAt : undefined,
    });
  }
  const pages: OpenPage[] = [];
  for (const row of Array.isArray(payload.pages) ? payload.pages : []) {
    if (
      !row ||
      typeof row !== 'object' ||
      !httpUrl(row.url) ||
      typeof row.title !== 'string' ||
      row.status !== 'done'
    )
      continue;
    pages.push({
      url: row.url,
      title: row.title,
      status: 'done',
      contentLength: typeof row.contentLength === 'number' ? row.contentLength : undefined,
    });
  }
  return {
    status,
    sources,
    pages,
    total:
      typeof payload.total === 'number' && Number.isFinite(payload.total)
        ? Math.max(0, Math.floor(payload.total))
        : 0,
    queries: Array.isArray(payload.queries)
      ? payload.queries.filter((q): q is string => typeof q === 'string')
      : [],
    browsing: payload.browsing === true,
    notice: typeof payload.notice === 'string' ? payload.notice : '',
  };
}
