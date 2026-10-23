import { useEffect, useState } from 'react';
import { Globe } from 'lucide-react';
import SearchSources, { SourceMark, sourceHost } from './SearchSources';
import type { WebSearchState } from './webSearch';

export default function ChatSearch({
  search,
  active,
}: {
  search: WebSearchState;
  active: boolean;
}) {
  const [tick, setTick] = useState(0);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [allPages, setAllPages] = useState(false);
  const searching = active && search.status === 'searching';
  const queries = search.queries;
  useEffect(() => {
    if (!searching || queries.length < 2) return;
    const id = setInterval(() => setTick((n) => n + 1), 1200);
    return () => clearInterval(id);
  }, [searching, queries.length]);
  const hosts = new Set<string>();
  const marks = search.sources
    .filter((source) => {
      const host = sourceHost(source);
      if (hosts.has(host)) return false;
      hosts.add(host);
      return true;
    })
    .slice(0, 4);
  const label =
    search.status === 'stopped'
      ? '搜索已停止'
      : search.status === 'error'
        ? '联网搜索暂不可用'
        : search.status === 'searching'
          ? '搜索中...'
          : search.total > 0
            ? `搜索到 ${search.total} 个网页`
            : '未搜索到合适内容';
  return (
    <div className="chat-web-search">
      <button
        className="search-step"
        data-status={search.status}
        onClick={() => setSourcesOpen(true)}
        disabled={!search.sources.length}
      >
        <Globe size={16} className={searching ? 'searching' : ''} />
        <span>{label}</span>
        <span className="source-marks">
          {marks.map((source) => (
            <SourceMark key={source.url} source={source} />
          ))}
        </span>
      </button>
      {searching && queries.length > 0 && (
        <div className="search-query" title={queries[tick % queries.length]}>
          {queries[tick % queries.length]}
        </div>
      )}
      {search.pages.length > 0 && (
        <div className="open-pages" data-status={search.browsing ? 'pending' : 'done'}>
          <div className="open-pages-head">
            <Globe size={16} />
            <span>{search.browsing ? '浏览页面中...' : `浏览 ${search.pages.length} 个页面`}</span>
          </div>
          <ul className="open-pages-list">
            {(allPages ? search.pages : search.pages.slice(0, 5)).map((page) => (
              <li key={page.url}>
                <a href={page.url} target="_blank" rel="noreferrer" title={page.title}>
                  <span className="open-pages-title">{page.title}</span>
                  <span aria-hidden="true">↗</span>
                </a>
              </li>
            ))}
          </ul>
          {!allPages && search.pages.length > 5 && (
            <button className="open-pages-more" onClick={() => setAllPages(true)}>
              查看全部（{search.pages.length}）
            </button>
          )}
        </div>
      )}
      {search.notice && search.status !== 'stopped' && (
        <p className="search-notice" role="status">
          {search.notice}
        </p>
      )}
      <SearchSources
        sources={search.sources}
        open={sourcesOpen}
        onClose={() => setSourcesOpen(false)}
      />
    </div>
  );
}
