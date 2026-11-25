import { useCallback, useLayoutEffect, useRef } from 'react';
import ChatReasoning from './ChatReasoning';
import ThinkingWave from './ThinkingWave';
import { IconThinking, IconChevronDown } from './dswui/Icons';
import type { ChatItem } from './updates';
import ChatSearch from './ChatSearch';
import ChatTool from './ChatTool';
import ChatContent from './ChatContent';
import type { WebSource } from './webSearch';

export default function ChatProcess({
  disclosureKey,
  disclosures,
  onDisclosure,
  entries,
  sources,
  running,
  active,
  loading,
  onGrowth,
}: {
  disclosureKey: string;
  disclosures: Record<string, boolean>;
  onDisclosure: (key: string, expanded: boolean) => void;
  entries: ChatItem[];
  sources?: WebSource[];
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
    if (running && active) scrollLatest();
    onGrowth();
  }, [scrollLatest, onGrowth, running, active]);
  useLayoutEffect(() => {
    if (active) grow();
  }, [entries, active, grow]);
  return (
    <details
      open={
        disclosures[disclosureKey] ??
        Boolean(!loading && (running || entries.some((entry) => entry.live)))
      }
      className={`think chat-process ${running ? 'thinking' : ''}`}
      data-live={entries.some((entry) => entry.live) || undefined}
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        onDisclosure(disclosureKey, event.currentTarget.open);
        followedTop.current = viewport.current?.scrollTop ?? 0;
        if (running) scrollLatest();
        else if (viewport.current) viewport.current.scrollTop = 0;
      }}
    >
      <summary className="think-head">
        <IconThinking className="think-icon" />
        <span className="think-label">思考与工具</span>
        <span className="chat-process-status">
          {running && <ThinkingWave active={active && !loading} />}
          {running ? '进行中' : `${entries.length} 项`}
        </span>
        <span className="caret">
          <IconChevronDown width={14} height={14} />
        </span>
      </summary>
      <div
        ref={viewport}
        className="think-body chat-process-items"
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
          entry.kind === 'search' && entry.search ? (
            <ChatSearch
              key={entry.id}
              search={entry.search}
              active={active && running && !loading}
            />
          ) : entry.kind === 'assistant' ? (
            <div key={entry.id} className="chat-progress-text" aria-label="执行说明">
              <ChatContent
                text={entry.text}
                live={false}
                streaming={Boolean(entry.live && !entry.final && active && !loading)}
                onGrowth={grow}
                sources={sources}
              />
            </div>
          ) : entry.kind === 'thinking' ? (
            <ChatReasoning
              expanded={disclosures[`${disclosureKey}:${entry.id}`]}
              onDisclosure={(expanded) => onDisclosure(`${disclosureKey}:${entry.id}`, expanded)}
              key={entry.id}
              entry={entry}
              running={running}
              active={active}
              loading={loading}
              onGrowth={grow}
            />
          ) : (
            <ChatTool key={entry.id} entry={entry} onGrowth={onGrowth} />
          ),
        )}
      </div>
    </details>
  );
}
