import { useState } from 'react';
import { Button } from '@icarus/ui';
import type { MemoryEntry } from './types';

function ChangedText({ before, after }: { before: string; after: string }) {
  const old = Array.from(before);
  const next = Array.from(after);
  let start = 0;
  while (start < old.length && start < next.length && old[start] === next[start]) start++;
  let end = 0;
  while (
    end < old.length - start &&
    end < next.length - start &&
    old[old.length - end - 1] === next[next.length - end - 1]
  )
    end++;
  const removed = old.slice(start, old.length - end).join('');
  const added = next.slice(start, next.length - end).join('');
  return (
    <>
      <div className="memory-diff" aria-label="正文变化">
        {removed && (
          <p>
            <span>删除</span>
            <del>{removed}</del>
          </p>
        )}
        {added && (
          <p>
            <span>新增</span>
            <ins>{added}</ins>
          </p>
        )}
      </div>
      <details>
        <summary>查看修改前后全文</summary>
        <h4>修改前</h4>
        <blockquote>{before}</blockquote>
        <h4>修改后</h4>
        <blockquote>{after}</blockquote>
      </details>
    </>
  );
}

export default function MemoryHistory({ entries }: { entries: MemoryEntry['history'] }) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(entries.length / 10));
  const current = Math.min(page, pages);
  return (
    <details className="memory-history">
      <summary>
        变更记录 <span>{entries.length}</span>
      </summary>
      {!entries.length && <p className="field-hint">暂无变更记录。</p>}
      <div className="memory-timeline">
        {entries.slice((current - 1) * 10, current * 10).map((entry, index) => (
          <div className="timeline-item" key={`${current}-${index}-${entry.date}`}>
            <i />
            <div>
              <p>
                <span>{entry.label}</span>
                <time>{entry.date}</time>
              </p>
              {entry.details?.map((detail) => (
                <p key={detail} className="field-hint">
                  {detail}
                </p>
              ))}
              {entry.content !== undefined &&
              entry.previous !== undefined &&
              entry.content !== entry.previous ? (
                <ChangedText before={entry.previous} after={entry.content} />
              ) : entry.content && entry.previous === undefined ? (
                <details>
                  <summary>查看记录正文</summary>
                  <blockquote>{entry.content}</blockquote>
                </details>
              ) : (
                <small className="field-hint">正文未变更</small>
              )}
            </div>
          </div>
        ))}
      </div>
      {pages > 1 && (
        <nav className="memory-history-pages" aria-label="变更记录分页">
          <Button disabled={current === 1} onClick={() => setPage(current - 1)}>
            上一页
          </Button>
          <span role="status">
            第 {current} / {pages} 页 · 共 {entries.length} 条
          </span>
          <Button disabled={current === pages} onClick={() => setPage(current + 1)}>
            下一页
          </Button>
        </nav>
      )}
    </details>
  );
}
