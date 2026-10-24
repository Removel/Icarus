import { memo } from 'react';
import ChatContent from './ChatContent';
import ChatMessageActions from './ChatMessageActions';
import type { ChatItem } from './updates';
import type { WebSource } from './webSearch';

export default memo(function ChatMessage({
  item,
  active,
  loading,
  disabled,
  prompt,
  sources,
  onGrowth,
  onCompose,
  anchor,
}: {
  item: ChatItem;
  active: boolean;
  loading: boolean;
  disabled: boolean;
  prompt?: string;
  sources?: WebSource[];
  onGrowth: () => void;
  onCompose: (text: string) => void;
  anchor: (id: string, node: HTMLElement | null) => void;
}) {
  return (
    <article
      ref={(node) => anchor(item.id, node)}
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
            streaming={Boolean(
              item.kind === 'assistant' && item.live && !item.final && active && !loading,
            )}
            onGrowth={onGrowth}
            sources={sources}
          />
        </div>
      )}
      {item.label && <small className="chat-message-state">{item.label}</small>}
      {(item.kind === 'user' || (item.kind === 'assistant' && item.final)) && item.text && (
        <ChatMessageActions
          text={item.text}
          user={item.kind === 'user'}
          onCompose={onCompose}
          disabled={disabled}
          prompt={prompt}
        />
      )}
    </article>
  );
});
