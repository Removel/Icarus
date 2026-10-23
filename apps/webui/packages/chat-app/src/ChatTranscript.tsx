import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import ChatContent from './ChatContent';
import ChatMessageActions from './ChatMessageActions';
import ChatProcess from './ChatProcess';
import { IconChevronDown, IconThinking } from './dswui/Icons';
import { ScrollNav } from './dswui/ScrollNav';
import { emptyTranscript } from './updates';

export default function ChatTranscript({
  transcript,
  active,
  sessionId,
  loading,
  submitting,
  onCompose,
}: {
  transcript: ReturnType<typeof emptyTranscript>;
  active: boolean;
  sessionId: string;
  loading: boolean;
  submitting: boolean;
  onCompose: (text: string) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const followedTop = useRef(0);
  const anchors = useRef(new Map<string, HTMLElement>());
  const [away, setAway] = useState(false);
  const [position, setPosition] = useState<string | null>(null);
  const lastUser = transcript.items.map((item) => item.kind).lastIndexOf('user');
  const responseKinds = ['assistant', 'thinking', 'tool', 'error', 'search'];
  const waiting =
    !loading &&
    (submitting
      ? !transcript.items.slice(lastUser + 1).some((item) => responseKinds.includes(item.kind))
      : transcript.tasks.some(
          (task) =>
            !transcript.items.some(
              (item) => item.taskId === task && responseKinds.includes(item.kind),
            ),
        ));
  const onGrowth = useCallback(() => {
    const node = scroll.current;
    if (!follow.current || !node || !node.clientHeight) return;
    if (node.scrollTop < followedTop.current - 2) {
      follow.current = false;
      setAway(true);
      return;
    }
    node.scrollTop = node.scrollHeight;
    followedTop.current = node.scrollTop;
  }, []);
  useLayoutEffect(() => {
    follow.current = true;
    followedTop.current = 0;
    setAway(false);
    setPosition(null);
  }, [sessionId]);
  useLayoutEffect(() => {
    if (active) onGrowth();
  }, [transcript.items, active, waiting, onGrowth]);
  function jump(id: string) {
    const node = anchors.current.get(id);
    const container = scroll.current;
    if (!node || !container) return;
    follow.current = false;
    container.scrollTop +=
      node.getBoundingClientRect().top - container.getBoundingClientRect().top - 20;
    node.focus({ preventScroll: true });
    setPosition(id);
    setAway(container.scrollHeight - container.scrollTop - container.clientHeight >= 64);
  }
  const processes = new Map<string, typeof transcript.items>();
  for (const item of transcript.items)
    if (item.kind === 'thinking' || item.kind === 'tool' || item.kind === 'search') {
      const key = item.taskId ?? item.id;
      processes.set(key, [...(processes.get(key) ?? []), item]);
    }
  const rendered = new Set<string>();
  const outline = transcript.items
    .filter((item) => item.kind === 'user')
    .map((item) => ({ id: item.id, title: item.text }));
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
        onScroll={() => {
          const node = scroll.current!;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
          if (follow.current) followedTop.current = node.scrollTop;
          setAway(!follow.current);
        }}
      >
        <div className="chat-inner">
          {loading && !transcript.items.length && <div role="status">正在加载对话</div>}
          {transcript.items.map((item, index) => {
            if (item.kind === 'thinking' || item.kind === 'tool' || item.kind === 'search') {
              const key = item.taskId ?? item.id;
              if (rendered.has(key)) return null;
              rendered.add(key);
              return (
                <ChatProcess
                  key={key}
                  entries={processes.get(key)!}
                  running={transcript.tasks.includes(key)}
                  active={active}
                  loading={loading}
                  onGrowth={onGrowth}
                />
              );
            }
            return (
              <article
                key={item.id}
                ref={(node) => {
                  if (node) anchors.current.set(item.id, node);
                  else anchors.current.delete(item.id);
                }}
                tabIndex={-1}
                className={`msg ${item.kind === 'user' ? 'msg-user' : 'msg-ai'} chat-message chat-${item.kind}`}
                data-live={(item.kind === 'assistant' && item.live) || undefined}
              >
                <strong className="dsw-sr-only">
                  {item.kind === 'user' ? '你' : item.kind === 'error' ? '执行失败' : 'Icarus'}
                </strong>
                {item.kind === 'user' ? (
                  <div className="bubble">{item.text}</div>
                ) : (
                  <div className={item.kind === 'error' ? 'error-card' : 'body'}>
                    <ChatContent
                      text={item.text}
                      live={Boolean(item.kind === 'assistant' && item.live && active && !loading)}
                      onGrowth={onGrowth}
                      sources={
                        transcript.items.find(
                          (entry) => entry.taskId === item.taskId && entry.search,
                        )?.search?.sources
                      }
                    />
                  </div>
                )}
                {item.label && <small className="chat-message-state">{item.label}</small>}
                {(item.kind === 'user' || (item.kind === 'assistant' && item.final)) &&
                  item.text && (
                    <ChatMessageActions
                      text={item.text}
                      user={item.kind === 'user'}
                      onCompose={onCompose}
                      disabled={submitting || loading || transcript.tasks.length > 0}
                      prompt={
                        transcript.items
                          .slice(0, index)
                          .reverse()
                          .find((entry) => entry.kind === 'user')?.text
                      }
                    />
                  )}
              </article>
            );
          })}
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
            follow.current = true;
            setAway(false);
            if (scroll.current) {
              scroll.current.scrollTop = scroll.current.scrollHeight;
              followedTop.current = scroll.current.scrollTop;
            }
          }}
        >
          <IconChevronDown width={14} height={14} />
        </button>
      )}
    </div>
  );
}
