import { useLayoutEffect, useRef, useState } from 'react';
import ChatContent from './ChatContent';
import useChatText from './useChatText';
import type { ChatItem } from './updates';

export default function ChatReasoning({
  entry,
  running,
  active,
  loading,
  onGrowth,
}: {
  entry: ChatItem;
  running: boolean;
  active: boolean;
  loading: boolean;
  onGrowth: () => void;
}) {
  const node = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(Boolean(entry.live && running));
  // Keep presentation state outside the collapsible content so reopening
  // a thought does not replay text that has already arrived.
  const content = useChatText(entry.text, Boolean(entry.live && active && !loading), onGrowth);
  useLayoutEffect(() => {
    if (!node.current) return;
    const observer = new ResizeObserver(onGrowth);
    observer.observe(node.current);
    return () => observer.disconnect();
  }, [onGrowth]);
  return (
    <div ref={node} className="chat-message chat-thinking">
      <details
        open={expanded}
        onToggle={(event) => {
          if (event.target !== event.currentTarget) return;
          setExpanded(event.currentTarget.open);
          onGrowth();
        }}
      >
        <summary>{entry.final || !running ? '已思考' : '正在思考'}</summary>
        <div className="chat-reasoning-text" data-streaming={content !== entry.text || undefined}>
          <ChatContent text={content} live={false} onGrowth={onGrowth} />
        </div>
      </details>
    </div>
  );
}
