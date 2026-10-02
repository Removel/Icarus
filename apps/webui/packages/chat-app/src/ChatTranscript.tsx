import { useEffect, useRef, useState } from 'react';
import { ArrowDown, MessageSquare } from 'lucide-react';
import { Button, EmptyState } from '@icarus/ui';
import { emptyTranscript } from './updates';

export default function ChatTranscript({
  transcript,
  active,
  sessionId,
}: {
  transcript: ReturnType<typeof emptyTranscript>;
  active: boolean;
  sessionId: string;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  useEffect(() => {
    follow.current = true;
    setAway(false);
  }, [sessionId]);
  useEffect(() => {
    if (active && follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [transcript.items, active]);
  return (
    <div className="chat-transcript-frame">
      <div
        ref={scroll}
        className="chat-transcript"
        role="log"
        aria-label="对话记录"
        aria-live="polite"
        onScroll={() => {
          const node = scroll.current!;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
          setAway(!follow.current);
        }}
      >
        {!transcript.items.length && (
          <div className="chat-welcome">
            <EmptyState
              icon={<MessageSquare size={32} aria-hidden="true" />}
              title={sessionId ? '开始这段对话' : '选择或新建会话'}
              description={
                sessionId
                  ? '写下问题或任务，思考过程与工具执行会随对话展开。'
                  : '连接工作区后，从左侧继续已有对话，或创建一个新会话。'
              }
            />
          </div>
        )}
        {transcript.items.map((item) =>
          item.kind === 'thinking' || item.kind === 'tool' ? (
            <details className={`chat-message chat-${item.kind}`} key={item.id}>
              <summary>
                {item.kind === 'thinking' ? '思考过程' : `${item.label} · ${item.text}`}
              </summary>
              <pre>{item.kind === 'thinking' ? item.text : item.detail}</pre>
            </details>
          ) : (
            <article className={`chat-message chat-${item.kind}`} key={item.id}>
              <strong>
                {item.kind === 'user' ? '你' : item.kind === 'error' ? '执行失败' : 'Icarus'}
              </strong>
              <div>{item.text}</div>
            </article>
          ),
        )}
      </div>
      {away && (
        <Button
          className="chat-latest"
          icon={<ArrowDown size={14} />}
          onClick={() => {
            follow.current = true;
            setAway(false);
            if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
          }}
        >
          回到最新消息
        </Button>
      )}
    </div>
  );
}
