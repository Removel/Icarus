import { lazy, Suspense, useLayoutEffect, useRef } from 'react';
import useChatText from './useChatText';
import type { ChatItem } from './updates';

const Reasoning = lazy(() =>
  import('@icarus/ui/ai').then(({ AIChatReasoning }) => ({ default: AIChatReasoning })),
);

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
  // Keep presentation state outside Semi's collapsible content so reopening
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
      <Suspense
        fallback={
          <div className="chat-reasoning-pending" role="status">
            正在思考中
          </div>
        }
      >
        <Reasoning
          status={entry.final || !running ? 'completed' : 'in_progress'}
          customRenderer={() => (
            <pre
              data-streaming={content !== entry.text || undefined}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {content}
            </pre>
          )}
        />
      </Suspense>
    </div>
  );
}
