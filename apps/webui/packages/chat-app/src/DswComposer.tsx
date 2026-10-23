// dswui Composer / ComposerToolbar presentation, using Icarus's controlled draft.
import { useLayoutEffect, useRef, useState } from 'react';
import type useChatSession from './useChatSession';
import { IconArrowUp, IconPaperclip, IconPencil, IconStop, IconTrash } from './dswui/Icons';
import { AnimatedToggleIcon } from './dswui/AnimatedToggleIcon';

export default function DswComposer({
  chat,
  enterToSend,
  onConnect,
}: {
  chat: ReturnType<typeof useChatSession>;
  enterToSend: boolean;
  onConnect: () => void;
}) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);
  const { connected, busy, submitting, loading, unknown, draft, setDraft, transcript } = chat;
  const running = submitting || transcript.tasks.length > 0;
  useLayoutEffect(() => {
    const node = textarea.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(336, Math.max(60, node.scrollHeight))}px`;
  }, [draft]);
  const send = () => {
    if (!connected) onConnect();
    else void chat.send();
  };
  const sendLabel = unknown ? '重试发送' : running || chat.queue.length > 0 ? '排队发送' : '发送';
  return (
    <form
      className="composer-wrap chat-compose"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <div className="composer-inner">
        {chat.queue.length > 0 && (
          <div className="chat-queue" aria-label="待发送消息">
            <div className="chat-queue-heading">
              <span>
                待发送 · {chat.queue.length}
                {chat.queuePaused ? ' · 已暂停' : ''}
              </span>
              {chat.queuePaused && (
                <button
                  type="button"
                  className="mini-btn"
                  disabled={!connected || busy || submitting || unknown}
                  onClick={chat.resumeQueue}
                >
                  继续队列
                </button>
              )}
            </div>
            <ol>
              {chat.queue.map((item, index) => (
                <li key={item.id}>
                  <span className="chat-queue-number">{index + 1}</span>
                  <span className="chat-queue-text" title={item.text}>
                    {item.text}
                  </span>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`编辑待发送消息 ${index + 1}`}
                    onClick={() => chat.editQueued(item.id)}
                  >
                    <IconPencil />
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`删除待发送消息 ${index + 1}`}
                    onClick={() => chat.removeQueued(item.id)}
                  >
                    <IconTrash />
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}
        <div className={`composer ${focused ? 'focused' : ''}`}>
          <textarea
            ref={textarea}
            aria-label="发送消息"
            value={draft}
            rows={2}
            placeholder="给 Icarus 发送消息"
            onChange={(event) => setDraft(event.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            disabled={busy || unknown || loading}
            onKeyDown={(event) => {
              if (
                event.nativeEvent.isComposing ||
                event.nativeEvent.keyCode === 229 ||
                event.key !== 'Enter' ||
                event.shiftKey
              )
                return;
              if (enterToSend || event.ctrlKey || event.metaKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-bar">
            <button
              type="button"
              className="pill"
              disabled
              title="思考能力由 Icarus 服务端配置，当前接口不提供前端开关"
            >
              <AnimatedToggleIcon kind="thinking" enabled={false} />
              深度思考
            </button>
            <button
              type="button"
              className="pill"
              disabled
              title="搜索工具由 Icarus 服务端配置，当前接口不提供前端开关"
            >
              <AnimatedToggleIcon kind="search" enabled={false} />
              智能搜索
            </button>
            <div className="spacer" />
            <button
              type="button"
              className="composer-attach"
              disabled
              title="当前 Gateway 尚无附件上传接口"
              aria-label="添加附件"
            >
              <IconPaperclip />
            </button>
            {running && (
              <button
                type="button"
                className="send-btn stop"
                disabled={!connected || busy}
                onClick={chat.cancel}
                title="停止生成"
                aria-label="停止执行"
              >
                <IconStop />
              </button>
            )}
            {(!running || draft.trim()) && (
              <button
                type="submit"
                className="send-btn"
                disabled={loading || busy || (unknown && submitting) || !draft.trim()}
                title={sendLabel}
                aria-label={sendLabel}
              >
                <IconArrowUp />
              </button>
            )}
          </div>
        </div>
        {(unknown || (transcript.tasks.length > 0 && Boolean(draft.trim()))) && (
          <div className="chat-runtime-actions">
            {unknown ? (
              <button
                type="button"
                className="mini-btn"
                disabled={busy || submitting}
                onClick={chat.clearDraft}
              >
                已核对历史，清除草稿
              </button>
            ) : (
              <button
                type="button"
                className="mini-btn"
                disabled={!connected || loading || busy || submitting || !draft.trim()}
                onClick={() => void chat.send(true)}
              >
                引导当前任务
              </button>
            )}
          </div>
        )}
        <div className="disclaimer">内容由 AI 生成，请仔细甄别</div>
      </div>
    </form>
  );
}
