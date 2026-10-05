import { useCallback, useLayoutEffect, useRef } from 'react';
import ChatReasoning from './ChatReasoning';
import ThinkingWave from './ThinkingWave';
import type { ChatItem } from './updates';

export default function ChatProcess({
  entries,
  running,
  active,
  loading,
  onGrowth,
}: {
  entries: ChatItem[];
  running: boolean;
  active: boolean;
  loading: boolean;
  onGrowth: () => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const followedTop = useRef(0);
  const scrollLatest = useCallback(() => {
    const node = viewport.current;
    if (!follow.current || !node || !node.clientHeight) return;
    if (node.scrollTop < followedTop.current - 2) {
      follow.current = false;
      return;
    }
    node.scrollTop = node.scrollHeight;
    followedTop.current = node.scrollTop;
  }, []);
  const grow = useCallback(() => {
    scrollLatest();
    onGrowth();
  }, [scrollLatest, onGrowth]);
  useLayoutEffect(() => {
    if (active) grow();
  }, [entries, active, grow]);
  return (
    <details
      className="chat-process"
      data-live={entries.some((entry) => entry.live) || undefined}
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        followedTop.current = viewport.current?.scrollTop ?? 0;
        scrollLatest();
      }}
    >
      <summary>
        思考与工具
        <span className="chat-process-status">
          {running && <ThinkingWave active={active && !loading} />}
          {running ? '进行中' : `${entries.length} 项`}
        </span>
      </summary>
      <div
        ref={viewport}
        className="chat-process-items"
        tabIndex={0}
        aria-label="思考与工具明细"
        onWheel={(event) => {
          if (event.deltaY < 0) {
            follow.current = false;
          }
        }}
        onScroll={() => {
          const node = viewport.current!;
          if (!node.clientHeight) return;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 2;
          if (follow.current) followedTop.current = node.scrollTop;
        }}
      >
        {entries.map((entry) =>
          entry.kind === 'thinking' ? (
            <ChatReasoning
              key={entry.id}
              entry={entry}
              running={running}
              active={active}
              loading={loading}
              onGrowth={grow}
            />
          ) : (
            <details className={`chat-message chat-${entry.kind}`} key={entry.id} onToggle={grow}>
              <summary>{`${entry.label} · ${entry.text}`}</summary>
              <pre>{entry.detail}</pre>
            </details>
          ),
        )}
      </div>
    </details>
  );
}
