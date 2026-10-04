import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { Pencil, Plus, Send, Trash2 } from 'lucide-react';
import { Button, Input, TextArea, Modal, SearchField, Tooltip } from '@icarus/ui';
import type { Session } from './gateway';
import useChatSession from './useChatSession';
import ChatTranscript from './ChatTranscript';
import './chat.css';

const titleOf = (session: Session) => session.title || session.first_user_input || '新会话';
function sessionTime(date?: string | null) {
  if (!date || !Number.isFinite(Date.parse(date))) return '';
  const days = Math.max(0, Math.floor((Date.now() - Date.parse(date)) / 86400000));
  return days === 0 ? '今天' : days === 1 ? '1 天前' : `${days} 天前`;
}

function SessionTitle({ title }: { title: string }) {
  const viewport = useRef<HTMLSpanElement>(null);
  const [distance, setDistance] = useState(0);
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const measure = () => setDistance(Math.max(0, node.scrollWidth - node.clientWidth));
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [title]);
  return (
    <span
      ref={viewport}
      className={`chat-session-title${distance > 0 ? ' is-overflowing' : ''}`}
      style={
        {
          '--title-travel': `-${distance}px`,
          '--title-duration': `${Math.max(2.8, distance / 70 + 1.5)}s`,
        } as CSSProperties
      }
    >
      <span className="chat-session-title-text">
        <span className="chat-title-change" key={title}>
          {title}
        </span>
      </span>
    </span>
  );
}

export default function ChatApp({ hash, active }: { hash: string; active: boolean }) {
  const chat = useChatSession(hash);
  const [search, setSearch] = useState('');
  const [deleting, setDeleting] = useState<Session>();
  const {
    workspace,
    sessionId,
    path,
    setPath,
    sessions,
    transcript,
    connected,
    loading,
    error,
    draft,
    setDraft,
    busy,
    submitting,
    unknown,
    guard,
  } = chat;
  useEffect(() => {
    setDeleting(undefined);
  }, [workspace]);
  const filtered = sessions.filter((session) =>
    (titleOf(session) + ' ' + session.first_user_input)
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  const selected = sessions.find((session) => session.session_id === sessionId);
  const sessionList = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!active) return;
    const list = sessionList.current;
    const current = list?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!list || !current) return;
    const bounds = list.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    if (
      item.top < bounds.top ||
      item.bottom > bounds.bottom ||
      item.left < bounds.left ||
      item.right > bounds.right
    )
      current.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [active, sessionId, sessions, search]);
  return (
    <section className="page chat-page" aria-label="Agent 对话">
      <form
        className="chat-connection"
        onSubmit={(event) => {
          event.preventDefault();
          if (path.trim()) chat.go({ workspace: path.trim() });
        }}
      >
        <Input
          aria-label="工作区路径"
          placeholder="服务端工作区的绝对路径"
          value={path}
          onChange={setPath}
          disabled={busy || submitting}
        />
        <Button htmlType="submit" disabled={!path.trim() || busy || submitting}>
          连接工作区
        </Button>
        <span className={`chat-connection-status ${connected ? 'is-connected' : ''}`} role="status">
          {connected ? '已连接' : workspace ? '未连接' : '选择工作区开始'}
        </span>
      </form>
      {error && (
        <div className="chat-error" role="alert">
          <p>{error}</p>
          <Button onClick={chat.reconnect}>重新连接</Button>
        </div>
      )}
      <div className="chat-workspace">
        <aside className="chat-sessions" aria-label="会话管理">
          <div className="chat-sessions-heading">
            <h2>会话</h2>
            <span>{sessions.length}</span>
            <Button
              theme="borderless"
              icon={<Plus size={16} />}
              aria-label="新建会话"
              title="新建会话"
              disabled={!connected || busy || submitting}
              onClick={() =>
                guard.requestLeave(() => {
                  chat.clearDraft();
                  void chat.createSession();
                })
              }
            />
          </div>
          <SearchField value={search} onChange={setSearch} placeholder="搜索会话…" />
          <nav ref={sessionList} className="chat-session-list" aria-label="会话列表">
            {filtered.map((session) => (
              <div className="chat-session-row" key={session.session_id}>
                <Tooltip
                  className="chat-session-popover"
                  showArrow={false}
                  motion={false}
                  position="right"
                  content={
                    <div className="chat-session-tooltip">
                      <strong>{titleOf(session)}</strong>
                      {session.updated_at && (
                        <span>
                          最近对话 · {sessionTime(session.updated_at)} ·{' '}
                          {new Date(session.updated_at).toLocaleString('zh-CN')}
                        </span>
                      )}
                      {session.created_at && (
                        <span>创建于 {new Date(session.created_at).toLocaleString('zh-CN')}</span>
                      )}
                    </div>
                  }
                >
                  <button
                    className={`chat-session ${session.session_id === sessionId ? 'is-selected' : ''}`}
                    aria-current={session.session_id === sessionId ? 'page' : undefined}
                    aria-label={titleOf(session)}
                    disabled={busy || submitting}
                    onClick={() => chat.go({ workspace, session: session.session_id })}
                  >
                    <SessionTitle title={titleOf(session)} />
                  </button>
                </Tooltip>
                <Button
                  className="chat-session-delete"
                  theme="borderless"
                  type="tertiary"
                  icon={<Trash2 size={14} />}
                  aria-label={`删除会话：${titleOf(session)}`}
                  disabled={
                    !connected ||
                    busy ||
                    submitting ||
                    (session.session_id === sessionId &&
                      (transcript.tasks.length > 0 || Boolean(draft) || chat.queue.length > 0))
                  }
                  onClick={() => setDeleting(session)}
                />
              </div>
            ))}
            {!filtered.length && (
              <p className="chat-session-empty">
                {search ? '没有匹配的会话' : connected ? '暂无会话' : ''}
              </p>
            )}
          </nav>
        </aside>
        <div className="chat-conversation">
          <header className="chat-conversation-heading">
            <h2>
              <span className="chat-title-change" key={selected ? titleOf(selected) : sessionId}>
                {selected ? titleOf(selected) : sessionId ? '新会话' : '对话'}
              </span>
            </h2>
            {(submitting || transcript.tasks.length > 0) && <span role="status">正在执行</span>}
          </header>
          <ChatTranscript
            key={workspace + sessionId}
            transcript={transcript}
            active={active}
            sessionId={sessionId}
            submitting={submitting}
            loading={loading || Boolean(workspace && !connected && !error)}
          />
          {sessionId && (
            <form
              className="chat-compose"
              onSubmit={(event) => {
                event.preventDefault();
                void chat.send();
              }}
            >
              {chat.queue.length > 0 && (
                <div className="chat-queue" aria-label="待发送消息">
                  <div className="chat-queue-heading">
                    <span>
                      待发送 · {chat.queue.length}
                      {chat.queuePaused ? ' · 已暂停' : ''}
                    </span>
                    {chat.queuePaused && (
                      <Button
                        theme="borderless"
                        size="small"
                        disabled={!connected || busy || submitting || unknown}
                        onClick={chat.resumeQueue}
                      >
                        继续队列
                      </Button>
                    )}
                  </div>
                  <ol>
                    {chat.queue.map((item, index) => (
                      <li key={item.id}>
                        <span className="chat-queue-number">{index + 1}</span>
                        <span className="chat-queue-text" title={item.text}>
                          {item.text}
                        </span>
                        <Button
                          theme="borderless"
                          size="small"
                          icon={<Pencil size={13} />}
                          aria-label={`编辑待发送消息 ${index + 1}`}
                          onClick={() => chat.editQueued(item.id)}
                        />
                        <Button
                          theme="borderless"
                          size="small"
                          icon={<Trash2 size={13} />}
                          aria-label={`删除待发送消息 ${index + 1}`}
                          onClick={() => chat.removeQueued(item.id)}
                        />
                      </li>
                    ))}
                  </ol>
                </div>
              )}
              <div className="chat-input-box">
                <TextArea
                  aria-label="发送消息"
                  placeholder="输入消息…"
                  value={draft}
                  rows={1}
                  onChange={setDraft}
                  disabled={busy || unknown || loading}
                  autosize={{ minRows: 1, maxRows: 5 }}
                  onKeyDown={(event) => {
                    if (
                      !event.nativeEvent.isComposing &&
                      !event.shiftKey &&
                      event.nativeEvent.keyCode !== 229 &&
                      event.key === 'Enter'
                    ) {
                      event.preventDefault();
                      void chat.send();
                    }
                  }}
                />
                <div className="chat-compose-actions">
                  {(submitting || transcript.tasks.length > 0) && (
                    <Button disabled={!connected || busy} onClick={chat.cancel}>
                      停止执行
                    </Button>
                  )}
                  {unknown && (
                    <Button disabled={busy || submitting} onClick={chat.clearDraft}>
                      已核对历史，清除草稿
                    </Button>
                  )}
                  {transcript.tasks.length > 0 && !unknown && (
                    <Button
                      disabled={!connected || loading || busy || submitting || !draft.trim()}
                      onClick={() => void chat.send(true)}
                    >
                      引导当前任务
                    </Button>
                  )}
                  <Button
                    htmlType="submit"
                    theme="solid"
                    icon={<Send size={14} />}
                    disabled={
                      !connected || loading || busy || (unknown && submitting) || !draft.trim()
                    }
                  >
                    {unknown
                      ? '重试发送'
                      : submitting || transcript.tasks.length > 0 || chat.queue.length > 0
                        ? '排队发送'
                        : '发送'}
                  </Button>
                </div>
              </div>
            </form>
          )}
        </div>
      </div>
      <Modal
        title="删除这段对话？"
        className="chat-confirm"
        width={360}
        closable={false}
        visible={Boolean(deleting)}
        onCancel={() => {
          if (!busy) setDeleting(undefined);
        }}
        maskClosable={!busy}
        footer={
          <>
            <Button disabled={busy} onClick={() => setDeleting(undefined)}>
              取消
            </Button>
            <Button
              type="danger"
              theme="solid"
              loading={busy}
              onClick={async () => {
                if (deleting && (await chat.deleteSession(deleting.session_id)))
                  setDeleting(undefined);
              }}
            >
              删除
            </Button>
          </>
        }
      >
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
      </Modal>
      <Modal
        title="放弃未发送的消息？"
        className="chat-confirm"
        width={360}
        closable={false}
        visible={guard.confirming}
        onCancel={guard.keepEditing}
        footer={
          <>
            <Button onClick={guard.keepEditing}>继续编辑</Button>
            <Button type="danger" theme="solid" disabled={busy} onClick={chat.discardDraft}>
              放弃
            </Button>
          </>
        }
      />
    </section>
  );
}
