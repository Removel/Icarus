import { IconClose } from './dswui/Icons';
import ChatDialog from './ChatDialog';
import type { WebSource } from './webSearch';
import { useEffect, useRef } from 'react';
import { ArrowUpRight } from 'lucide-react';

export function sourceHost(source: { url: string }) {
  return new URL(source.url).hostname.replace(/^www\./, '');
}

export function SourceMark({ source }: { source: WebSource }) {
  const host = sourceHost(source);
  const hue = [...host].reduce((value, char) => (value * 31 + char.charCodeAt(0)) % 360, 0);
  return (
    <span
      className="source-mark"
      style={{ backgroundColor: `hsl(${hue} 30% 45%)` }}
      title={host}
      aria-hidden="true"
    >
      {host[0].toUpperCase()}
    </span>
  );
}

export default function SearchSources({
  sources,
  open,
  selected,
  onClose,
}: {
  sources: WebSource[];
  open: boolean;
  selected?: number;
  onClose: () => void;
}) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (open && selected !== undefined)
      list.current?.querySelector('[data-selected]')?.scrollIntoView({ block: 'nearest' });
    else if (open) list.current?.scrollTo(0, 0);
  }, [open, selected]);
  return (
    <ChatDialog open={open} label="联网来源" onClose={onClose} side>
      <div className="dialog web-sources-panel">
        <div className="dialog-head">
          <h2>联网来源 · {sources.length}</h2>
          <button className="icon-btn" aria-label="关闭来源" onClick={onClose}>
            <IconClose />
          </button>
        </div>
        <ol className="web-sources-list" ref={list}>
          {sources.map((source) => (
            <li
              key={source.citeIndex}
              value={source.citeIndex}
              data-selected={selected === source.citeIndex || undefined}
            >
              <a href={source.url} target="_blank" rel="noreferrer" className="web-source-card">
                <span className="web-source-meta">
                  <SourceMark source={source} />
                  <span>{source.siteName || sourceHost(source)}</span>
                  <span className="web-source-index">{source.citeIndex}</span>
                </span>
                <strong>
                  {source.title}
                  <ArrowUpRight size={14} aria-hidden="true" />
                </strong>
                {source.publishedAt && <small>{source.publishedAt}</small>}
                {source.snippet && <p>{source.snippet}</p>}
              </a>
            </li>
          ))}
        </ol>
      </div>
    </ChatDialog>
  );
}
