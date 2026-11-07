import { useLayoutEffect, useRef } from 'react';
import ChatContent from './ChatContent';
import useChatText from './useChatText';
import type { ChatItem } from './updates';

export default function ChatReasoning({
  expanded,
  onDisclosure,
  entry,
  running,
  active,
  loading,
  onGrowth,
}: {
  expanded?: boolean;
  onDisclosure: (expanded: boolean) => void;
  entry: ChatItem;
  running: boolean;
  active: boolean;
  loading: boolean;
  onGrowth: () => void;
}) {
  const node = useRef<HTMLDivElement>(null);
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
        open={expanded ?? Boolean(!loading && (entry.live || running))}
        onToggle={(event) => {
          if (event.target !== event.currentTarget) return;
          onDisclosure(event.currentTarget.open);
          onGrowth();
        }}
      >
        <summary>{entry.final || !running ? '已思考' : '正在思考'}</summary>
        <div className="chat-reasoning-text" data-streaming={content !== entry.text || undefined}>
          <ChatContent
            text={content}
            live={false}
            streaming={Boolean(entry.live && (!entry.final || content !== entry.text))}
            onGrowth={onGrowth}
          />
        </div>
      </details>
    </div>
  );
}
