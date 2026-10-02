import { useState } from 'react';
import { MessageSquare, Plus, Send } from 'lucide-react';
import { Button, Input, TextArea, Modal, SearchField } from '@icarus/ui';
import useChatSession from './useChatSession';
import ChatTranscript from './ChatTranscript';
import './chat.css';

export default function ChatApp({ hash, active }: { hash: string; active: boolean }) {
  const chat = useChatSession(hash);
  const [search, setSearch] = useState('');
  const {
    workspace,
    sessionId,
    path,
    setPath,
    sessions,
    transcript,
    connected,
    error,
    draft,
    setDraft,
    busy,
    unknown,
    guard,
  } = chat;
  const filtered = sessions.filter((session) =>
    (session.first_user_input || session.session_id)
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  const selected = sessions.find((session) => session.session_id === sessionId);
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
          disabled={busy}
        />
        <Button htmlType="submit" disabled={!path.trim() || busy}>
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
              disabled={!connected || busy}
              onClick={chat.createSession}
            />
          </div>
          <SearchField value={search} onChange={setSearch} placeholder="搜索会话…" />
          <nav className="chat-session-list" aria-label="会话列表">
            {filtered.map((session) => (
              <button
                key={session.session_id}
                className={`chat-session ${session.session_id === sessionId ? 'is-selected' : ''}`}
                aria-current={session.session_id === sessionId ? 'page' : undefined}
                disabled={busy}
                onClick={() => chat.go({ workspace, session: session.session_id })}
              >
                <MessageSquare size={16} />
                <span>
                  {session.first_user_input || '新会话'}
                  <small>{session.session_id.slice(0, 12)}</small>
                </span>
              </button>
            ))}
            {!filtered.length && (
              <p className="chat-session-empty">
                {search
                  ? '没有匹配的会话'
                  : connected
                    ? '还没有会话，点击 + 开始。'
                    : '连接工作区后查看会话。'}
              </p>
            )}
          </nav>
        </aside>
        <div className="chat-conversation">
          <header className="chat-conversation-heading">
            <h2>{selected?.first_user_input || (sessionId ? '新会话' : '对话')}</h2>
            <span>
              {transcript.tasks.length ? '正在执行' : sessionId ? '可继续对话' : '等待选择会话'}
            </span>
          </header>
          <ChatTranscript transcript={transcript} active={active} sessionId={sessionId} />
          {sessionId && (
            <form
              className="chat-compose"
              onSubmit={(event) => {
                event.preventDefault();
                void chat.send();
              }}
            >
              <TextArea
                aria-label="发送消息"
                placeholder="输入消息，Ctrl / ⌘ + Enter 发送"
                value={draft}
                onChange={setDraft}
                disabled={busy || unknown}
                autosize={{ minRows: 2, maxRows: 5 }}
                onKeyDown={(event) => {
                  if (
                    !event.nativeEvent.isComposing &&
                    (event.ctrlKey || event.metaKey) &&
                    event.key === 'Enter'
                  ) {
                    event.preventDefault();
                    void chat.send();
                  }
                }}
              />
              <div className="chat-compose-actions">
                <span role="status">{transcript.tasks.length ? '正在执行' : '等待输入'}</span>
                {transcript.tasks.length > 0 && (
                  <Button disabled={!connected || busy} onClick={chat.cancel}>
                    停止执行
                  </Button>
                )}
                {unknown && (
                  <Button disabled={busy} onClick={chat.clearDraft}>
                    已核对历史，清除草稿
                  </Button>
                )}
                <Button
                  htmlType="submit"
                  theme="solid"
                  icon={<Send size={14} />}
                  disabled={
                    !connected || busy || !draft.trim() || (transcript.tasks.length > 0 && !unknown)
                  }
                >
                  {unknown ? '重试发送' : '发送'}
                </Button>
              </div>
            </form>
          )}
        </div>
      </div>
      <Modal
        title="保留消息草稿"
        visible={guard.confirming}
        onCancel={guard.keepEditing}
        footer={
          <>
            <Button onClick={guard.keepEditing}>继续编辑</Button>
            <Button disabled={busy} onClick={guard.discardChanges}>
              放弃草稿
            </Button>
          </>
        }
      >
        <p>消息尚未发送，离开前要放弃草稿吗？</p>
      </Modal>
    </section>
  );
}
