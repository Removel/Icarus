import { IconClose } from './dswui/Icons';
import ChatDialog from './ChatDialog';
import type { WebSource } from './webSearch';

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
  return (
    <ChatDialog open={open} label="联网来源" onClose={onClose} side>
      <div className="dialog web-sources-panel">
        <div className="dialog-head">
          <h2>联网来源 · {sources.length}</h2>
          <button className="icon-btn" aria-label="关闭来源" onClick={onClose}>
            <IconClose />
          </button>
        </div>
        <ol className="web-sources-list">
          {sources.map((source) => (
            <li
              key={source.citeIndex}
              value={source.citeIndex}
              data-selected={selected === source.citeIndex || undefined}
            >
              <a href={source.url} target="_blank" rel="noreferrer">
                <SourceMark source={source} />
                <span>
                  <strong>{source.title} ↗</strong>
                  <small>
                    {source.siteName || sourceHost(source)}
                    {source.publishedAt ? ` · ${source.publishedAt}` : ''}
                  </small>
                </span>
              </a>
              {source.snippet && <p>{source.snippet}</p>}
            </li>
          ))}
        </ol>
      </div>
    </ChatDialog>
  );
}
