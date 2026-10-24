import { memo } from 'react';
import ChatContent from './ChatContent';
import ChatMessageActions from './ChatMessageActions';
import type { ChatItem } from './updates';
import type { WebSource } from './webSearch';
import ChatImages from './ChatImages';
import type { ImageResource } from './imageResources';

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
  workspace,
  sessionId,
}: {
  item: ChatItem;
  active: boolean;
  loading: boolean;
  disabled: boolean;
  prompt?: string;
  sources?: WebSource[];
  onGrowth: () => void;
  onCompose: (text: string, resources?: ImageResource[]) => void;
  anchor: (id: string, node: HTMLElement | null) => void;
  workspace: string;
  sessionId: string;
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
        <div className="bubble">
          {item.resources?.length ? (
            <ChatImages
              resources={item.resources}
              workspace={workspace}
              session={sessionId}
              onGrowth={onGrowth}
            />
          ) : null}
          {item.text}
        </div>
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
      {(item.kind === 'user' || (item.kind === 'assistant' && item.final)) &&
        (item.text || item.resources?.length) && (
          <ChatMessageActions
            text={item.text}
            user={item.kind === 'user'}
            onCompose={onCompose}
            disabled={disabled}
            prompt={prompt}
            resources={item.resources}
          />
        )}
    </article>
  );
});
