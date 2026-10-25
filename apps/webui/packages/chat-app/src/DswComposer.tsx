// dswui Composer / ComposerToolbar presentation, using Icarus's controlled draft.
import { useLayoutEffect, useRef, useState } from 'react';
import type useChatSession from './useChatSession';
import { IconArrowUp, IconPaperclip, IconPencil, IconStop, IconTrash } from './dswui/Icons';
import { AnimatedToggleIcon } from './dswui/AnimatedToggleIcon';
import { imageTypes } from './imageResources';
import ChatImageInputs from './ChatImageInputs';

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
  const picker = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);
  const [dragging, setDragging] = useState(false);
  const { connected, busy, submitting, loading, unknown, draft, setDraft, transcript } = chat;
  const running = submitting || transcript.tasks.length > 0;
  const hasInput = Boolean(draft.trim() || chat.attachments.images.length);
  const attachmentDisabled = busy || unknown || loading;
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
      data-dragging={dragging || undefined}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        if (!attachmentDisabled) setDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        setDragging(false);
        if (!attachmentDisabled) chat.attachments.add(Array.from(event.dataTransfer.files));
      }}
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
                    {item.text || '图片消息'}
                    {item.resources?.length ? ` · ${item.resources.length} 张图片` : ''}
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
          <input
            ref={picker}
            type="file"
            multiple
            accept={imageTypes.join(',')}
            hidden
            aria-label="选择图片"
            onChange={(event) => {
              chat.attachments.add(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
          {dragging && (
            <div className="chat-image-drop" role="status">
              松开以添加图片
            </div>
          )}
          {chat.attachments.images.length > 0 && (
            <ChatImageInputs
              images={chat.attachments.images}
              disabled={attachmentDisabled}
              remove={chat.attachments.remove}
              retry={chat.attachments.retry}
            />
          )}
          {chat.attachments.error && (
            <div className="chat-image-upload-error" role="alert">
              {chat.attachments.error}
            </div>
          )}
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
            onPaste={(event) => {
              const files = Array.from(event.clipboardData.items)
                .filter((item) => item.kind === 'file')
                .flatMap((item) => {
                  const file = item.getAsFile();
                  return file ? [file] : [];
                });
              if (!files.length) return;
              event.preventDefault();
              if (!attachmentDisabled) {
                chat.attachments.add(files);
                const text = event.clipboardData.getData('text/plain');
                if (text) {
                  const node = event.currentTarget;
                  setDraft(
                    draft.slice(0, node.selectionStart) + text + draft.slice(node.selectionEnd),
                  );
                }
              }
            }}
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
              className={chat.webSearch ? 'pill on' : 'pill'}
              aria-pressed={chat.webSearch}
              disabled={unknown}
              onClick={() => chat.setWebSearch(!chat.webSearch)}
              title="允许按需联网：需要最新资料、查证或明确要求搜索时才检索；失败仍继续回答"
            >
              <AnimatedToggleIcon kind="search" enabled={chat.webSearch} />
              联网搜索
            </button>
            <div className="composer-actions">
              {unknown ? (
                <button
                  type="button"
                  className="pill composer-reconcile"
                  disabled={busy || submitting}
                  onClick={chat.clearDraft}
                  title="已核对历史，清除草稿"
                  aria-label="已核对历史，清除草稿"
                >
                  清除草稿
                </button>
              ) : transcript.tasks.length > 0 && hasInput ? (
                <button
                  type="button"
                  className="pill composer-steer"
                  disabled={!connected || loading || busy || submitting || !chat.imagesReady}
                  onClick={() => void chat.send(true)}
                  title="引导当前任务"
                  aria-label="引导当前任务"
                >
                  <IconPencil />
                  引导
                </button>
              ) : null}
              <button
                type="button"
                className="composer-attach"
                disabled={attachmentDisabled}
                title="添加图片，支持粘贴或拖入"
                aria-label="添加图片"
                onClick={() => picker.current?.click()}
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
              {(!running || hasInput) && (
                <button
                  type="submit"
                  className="send-btn"
                  disabled={
                    loading || busy || !chat.imagesReady || (unknown && submitting) || !hasInput
                  }
                  title={sendLabel}
                  aria-label={sendLabel}
                >
                  <IconArrowUp />
                </button>
              )}
            </div>
          </div>
        </div>
        <div className="disclaimer">内容由 AI 生成，请仔细甄别</div>
      </div>
    </form>
  );
}
