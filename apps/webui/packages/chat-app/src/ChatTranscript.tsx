import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import ChatMessage from './ChatMessage';
import ChatProcess from './ChatProcess';
import { IconChevronDown, IconThinking } from './dswui/Icons';
import { ScrollNav } from './dswui/ScrollNav';
import type { Transcript, ChatItem } from './updates';
import type { WebSource } from './webSearch';

type Row = {
  id: string;
  item: ChatItem;
  entries?: ChatItem[];
  prompt?: string;
  sources?: WebSource[];
};

export default function ChatTranscript({
  transcript,
  active,
  sessionId,
  loading,
  submitting,
  onCompose,
}: {
  transcript: Transcript;
  active: boolean;
  sessionId: string;
  loading: boolean;
  submitting: boolean;
  onCompose: (text: string) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const followedTop = useRef(0);
  const previousHeight = useRef(0);
  const measuredRows = useRef(new Map<string, number>());
  const frame = useRef<number | undefined>(undefined);
  const anchors = useRef(new Map<string, HTMLElement>());
  const pendingJump = useRef<string | undefined>(undefined);
  const [away, setAway] = useState(false);
  const [position, setPosition] = useState<string | null>(null);
  const { rows, outline, waiting } = useMemo(() => {
    const rows: Row[] = [];
    const processes = new Map<string, Row>();
    const sources = new Map<string | undefined, WebSource[]>();
    for (const item of transcript.items)
      if (item.search) sources.set(item.taskId, item.search.sources);
    let prompt: string | undefined;
    let lastUser = -1;
    const responding = new Set<string | undefined>();
    transcript.items.forEach((item, index) => {
      if (item.kind === 'user') {
        prompt = item.text;
        lastUser = index;
      } else responding.add(item.taskId);
      if (item.kind === 'thinking' || item.kind === 'tool' || item.kind === 'search') {
        const key = item.taskId ?? item.id;
        const row = processes.get(key);
        if (row) row.entries!.push(item);
        else {
          const row = { id: key, item, entries: [item] };
          processes.set(key, row);
          rows.push(row);
        }
      } else rows.push({ id: item.id, item, prompt, sources: sources.get(item.taskId) });
    });
    return {
      rows,
      outline: transcript.items
        .filter((item) => item.kind === 'user')
        .map((item) => ({ id: item.id, title: item.text })),
      waiting:
        !loading &&
        (submitting
          ? !transcript.items.slice(lastUser + 1).some((item) => item.kind !== 'user')
          : transcript.tasks.some((task) => !responding.has(task))),
    };
  }, [transcript.items, transcript.tasks, loading, submitting]);
  // Small conversations keep native full-document selection and browser find.
  const virtual = rows.length >= 80;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroll.current,
    getItemKey: (index) => rows[index].id,
    estimateSize: (index) =>
      measuredRows.current.get(rows[index].id) ??
      (rows[index].entries ? 56 : rows[index].item.kind === 'user' ? 100 : 200),
    overscan: 6,
    enabled: virtual && active,
    initialOffset: () => followedTop.current,
    scrollMargin: 8,
    useAnimationFrameWithResizeObserver: true,
  });
  const onGrowth = useCallback(() => {
    if (frame.current !== undefined) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = undefined;
      const node = scroll.current;
      if (!follow.current || !node || !node.clientHeight) return;
      node.scrollTop = node.scrollHeight;
      followedTop.current = node.scrollTop;
    });
  }, []);
  const anchor = useCallback((id: string, node: HTMLElement | null) => {
    if (node) anchors.current.set(id, node);
    else anchors.current.delete(id);
  }, []);
  useLayoutEffect(() => {
    follow.current = true;
    followedTop.current = 0;
    pendingJump.current = undefined;
    setAway(false);
    setPosition(null);
  }, [sessionId]);
  useLayoutEffect(() => {
    if (!active) return;
    // Paint cached history at the bottom immediately, before presentation frames.
    if (follow.current && scroll.current) {
      scroll.current.scrollTop = scroll.current.scrollHeight;
      followedTop.current = scroll.current.scrollTop;
    }
    onGrowth();
  }, [rows.length, sessionId, active, loading, onGrowth]);
  useLayoutEffect(() => {
    const node = content.current;
    if (!node || !active) return;
    const observer = new ResizeObserver(onGrowth);
    observer.observe(node);
    return () => observer.disconnect();
  }, [active, onGrowth]);
  useLayoutEffect(() => {
    if (virtual || !active || !content.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const id = (entry.target as HTMLElement).dataset.rowId;
        if (id)
          measuredRows.current.set(
            id,
            entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height,
          );
      }
    });
    measuredRows.current.clear();
    for (const row of content.current.querySelectorAll('.chat-row')) observer.observe(row);
    return () => observer.disconnect();
  }, [virtual, active, rows.length, sessionId]);
  useLayoutEffect(
    () => () => {
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
      frame.current = undefined;
    },
    [],
  );
  const visible = virtualizer.getVirtualItems();
  useLayoutEffect(() => {
    const id = pendingJump.current;
    const target = id && anchors.current.get(id);
    const container = scroll.current;
    if (!target || !container) return;
    pendingJump.current = undefined;
    virtualizer.cancelScroll();
    container.scrollTop +=
      target.getBoundingClientRect().top - container.getBoundingClientRect().top - 20;
    target.focus({ preventScroll: true });
  });
  function jump(id: string) {
    const node = anchors.current.get(id);
    const container = scroll.current;
    if (!container) return;
    follow.current = false;
    setAway(true);
    setPosition(id);
    if (node) {
      container.scrollTop +=
        node.getBoundingClientRect().top - container.getBoundingClientRect().top - 20;
      node.focus({ preventScroll: true });
    } else {
      pendingJump.current = id;
      virtualizer.scrollToIndex(
        rows.findIndex((row) => row.id === id),
        { align: 'start' },
      );
    }
  }
  const disabled = submitting || loading || transcript.tasks.length > 0;
  function renderRow(row: Row) {
    return row.entries ? (
      <ChatProcess
        entries={row.entries}
        running={transcript.tasks.includes(row.id)}
        active={active}
        loading={loading}
        onGrowth={onGrowth}
      />
    ) : (
      <ChatMessage
        item={row.item}
        active={active}
        loading={loading}
        disabled={disabled}
        prompt={row.prompt}
        sources={row.sources}
        onCompose={onCompose}
        onGrowth={onGrowth}
        anchor={anchor}
      />
    );
  }
  return (
    <div className="chat-transcript-frame">
      <ScrollNav items={outline} active={position} onJump={jump} />
      <div
        ref={scroll}
        className="scroll-area chat-transcript"
        role="log"
        aria-label="对话记录"
        aria-live="polite"
        aria-busy={loading}
        onWheel={(event) => {
          if (event.deltaY < 0) {
            virtualizer.cancelScroll();
            follow.current = false;
            setAway(true);
          }
        }}
        onTouchStart={() => {
          virtualizer.cancelScroll();
          follow.current = false;
        }}
        onScroll={() => {
          const node = scroll.current!;
          if (!active || !node.clientHeight) return;
          const resized = previousHeight.current !== node.scrollHeight;
          const bottom = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
          if (bottom) follow.current = true;
          else if (!resized && node.scrollTop < followedTop.current - 2) follow.current = false;
          previousHeight.current = node.scrollHeight;
          followedTop.current = node.scrollTop;
          setAway(!follow.current);
        }}
      >
        <div ref={content} className="chat-inner">
          {loading && !transcript.items.length && <div role="status">正在加载对话</div>}
          <div
            className={virtual ? 'chat-virtual-items' : undefined}
            data-virtualized={virtual || undefined}
            style={virtual ? { height: virtualizer.getTotalSize() } : undefined}
          >
            {virtual
              ? visible.map((item) => (
                  <div
                    key={item.key}
                    ref={virtualizer.measureElement}
                    data-index={item.index}
                    className="chat-virtual-row"
                    style={{ transform: `translateY(${item.start - 8}px)` }}
                  >
                    {renderRow(rows[item.index])}
                  </div>
                ))
              : rows.map((row) => (
                  <div className="chat-row" data-row-id={row.id} key={row.id}>
                    {renderRow(row)}
                  </div>
                ))}
          </div>
          {waiting && (
            <div className="think thinking chat-response-pending" role="status">
              <IconThinking className="think-icon" />
              <span className="think-label">正在思考中</span>
            </div>
          )}
          <div style={{ height: 8 }} />
        </div>
      </div>
      {away && (
        <button
          className="scroll-bottom chat-latest"
          title="回到底部"
          aria-label="回到最新消息"
          onClick={() => {
            virtualizer.cancelScroll();
            pendingJump.current = undefined;
            follow.current = true;
            setAway(false);
            onGrowth();
          }}
        >
          <IconChevronDown width={14} height={14} />
        </button>
      )}
    </div>
  );
}
