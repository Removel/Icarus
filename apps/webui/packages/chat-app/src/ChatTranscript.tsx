import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, List, MessageSquare } from 'lucide-react';
import { Button, LoadingIndicator, SearchField } from '@icarus/ui';
import ChatContent from './ChatContent';
import ChatProcess from './ChatProcess';
import ThinkingWave from './ThinkingWave';
import { emptyTranscript } from './updates';

export default function ChatTranscript({
  transcript,
  active,
  sessionId,
  loading,
  submitting,
}: {
  transcript: ReturnType<typeof emptyTranscript>;
  active: boolean;
  sessionId: string;
  loading: boolean;
  submitting: boolean;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const followedTop = useRef(0);
  const anchors = useRef(new Map<string, HTMLElement>());
  const [away, setAway] = useState(false);
  const [outline, setOutline] = useState(false);
  const [query, setQuery] = useState('');
  const [position, setPosition] = useState(-1);
  const responseKinds = ['assistant', 'thinking', 'tool', 'error'];
  const lastUser = transcript.items.map((item) => item.kind).lastIndexOf('user');
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
    if (!follow.current || !node) return;
    // A scroll event can arrive after the next presentation frame. Respect an
    // upward move immediately instead of overwriting it with that frame.
    if (node.scrollTop < followedTop.current - 2) {
      follow.current = false;
      setAway(true);
      return;
    }
    node.scrollTop = node.scrollHeight;
    followedTop.current = node.scrollTop;
  }, []);
  const messages = transcript.items.filter((item) =>
    ['user', 'assistant', 'error'].includes(item.kind),
  );
  const matches = messages.filter((item) =>
    item.text.toLowerCase().includes(query.trim().toLowerCase()),
  );
  useEffect(() => {
    follow.current = true;
    followedTop.current = 0;
    setAway(false);
    setOutline(false);
    setQuery('');
    setPosition(-1);
  }, [sessionId]);
  useEffect(() => {
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
    setPosition(messages.findIndex((item) => item.id === id));
    setAway(container.scrollHeight - container.scrollTop - container.clientHeight >= 64);
  }
  const processes = new Map<string, typeof transcript.items>();
  for (const item of transcript.items)
    if (item.kind === 'thinking' || item.kind === 'tool') {
      const key = item.taskId ?? item.id;
      processes.set(key, [...(processes.get(key) ?? []), item]);
    }
  const rendered = new Set<string>();
  return (
    <div className="chat-transcript-frame">
      {messages.length > 0 && (
        <div className="chat-navigation">
          <Button
            theme="borderless"
            icon={<ArrowUp size={14} />}
            aria-label="上一条消息"
            disabled={position === 0}
            onClick={() =>
              jump(messages[Math.max(0, (position < 0 ? messages.length : position) - 1)].id)
            }
          />
          <Button
            theme="borderless"
            icon={<List size={15} />}
            aria-label="定位消息"
            aria-expanded={outline}
            onClick={() => setOutline((value) => !value)}
          />
          <Button
            theme="borderless"
            icon={<ArrowDown size={14} />}
            aria-label="下一条消息"
            disabled={position >= messages.length - 1}
            onClick={() => jump(messages[Math.min(messages.length - 1, position + 1)].id)}
          />
          {outline && (
            <nav className="chat-message-outline" aria-label="消息定位">
              <SearchField value={query} onChange={setQuery} placeholder="搜索本段对话…" />
              <div className="chat-outline-items">
                {matches.map((item) => (
                  <button
                    key={item.id}
                    aria-current={messages[position]?.id === item.id ? 'location' : undefined}
                    onClick={() => {
                      jump(item.id);
                      setOutline(false);
                    }}
                  >
                    <small>
                      {messages.indexOf(item) + 1} ·{' '}
                      {item.kind === 'user' ? '你' : item.kind === 'error' ? '错误' : 'Icarus'}
                    </small>
                    <span>{item.text.replace(/[#*`]/g, '').slice(0, 100) || '…'}</span>
                  </button>
                ))}
                {!matches.length && <p>没有匹配的消息</p>}
              </div>
            </nav>
          )}
        </div>
      )}
      <div
        ref={scroll}
        className="chat-transcript"
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
        {loading ? (
          <LoadingIndicator label="正在加载对话" />
        ) : (
          !transcript.items.length && (
            <div className="chat-welcome">
              <MessageSquare size={28} aria-hidden="true" />
              <p>{sessionId ? '有什么可以帮你？' : '选择或新建对话'}</p>
            </div>
          )
        )}
        {transcript.items.map((item) => {
          if (item.kind === 'thinking' || item.kind === 'tool') {
            const key = item.taskId ?? item.id;
            if (rendered.has(key)) return null;
            rendered.add(key);
            const entries = processes.get(key)!;
            return (
              <ChatProcess
                key={key}
                entries={entries}
                running={transcript.tasks.includes(key)}
                active={active}
                loading={loading}
                onGrowth={onGrowth}
              />
            );
          }
          return (
            <article
              ref={(node) => {
                if (node) anchors.current.set(item.id, node);
                else anchors.current.delete(item.id);
              }}
              tabIndex={-1}
              className={`chat-message chat-${item.kind}`}
              data-live={(item.kind === 'assistant' && item.live) || undefined}
              key={item.id}
            >
              <strong>
                {item.kind === 'user' ? '你' : item.kind === 'error' ? '执行失败' : 'Icarus'}
              </strong>
              {item.kind === 'user' && item.label && (
                <small className="chat-message-state">{item.label}</small>
              )}
              <ChatContent
                text={item.text}
                live={Boolean(item.kind === 'assistant' && item.live && active && !loading)}
                onGrowth={onGrowth}
              />
            </article>
          );
        })}
        {waiting && (
          <div className="chat-response-pending" role="status">
            <ThinkingWave active={active} />
            <span>正在思考中</span>
          </div>
        )}
      </div>
      {away && (
        <Button
          className="chat-latest"
          icon={<ArrowDown size={14} />}
          onClick={() => {
            follow.current = true;
            setAway(false);
            if (scroll.current) {
              scroll.current.scrollTop = scroll.current.scrollHeight;
              followedTop.current = scroll.current.scrollTop;
            }
          }}
        >
          回到最新消息
        </Button>
      )}
    </div>
  );
}
