import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { BrandMark, transitionLayout, useNarrowLayout } from '@icarus/ui';
import type { History, Session } from './gateway';
import { applyUpdate, emptyTranscript } from './updates';
import useChatPreference from './useChatPreference';
import useChatSession from './useChatSession';
import ChatTranscript from './ChatTranscript';
import ChatSettings, { type ChatSettingsProps } from './ChatSettings';
import ChatDialog from './ChatDialog';
import DswSidebar, { titleOf } from './DswSidebar';
import DswComposer from './DswComposer';
import { IconClose, IconSearch } from './dswui/Icons';
import { pickWelcomeMessage } from './dswui/welcomeMessages';
import './dswui/styles/index.css';
import './chat-runtime.css';

function readEnterPreference() {
  try {
    return localStorage.getItem('icarus.chat.enterToSend') !== 'false';
  } catch {
    return true;
  }
}

export default function ChatApp({
  hash,
  active,
  managementPage,
  managementContent,
  ...settings
}: {
  hash: string;
  active: boolean;
  managementPage?: 'memory' | 'knowledge';
  managementContent?: ReactNode;
} & ChatSettingsProps) {
  const chat = useChatSession(hash);
  const narrow = useNarrowLayout();
  const [sidebarOpen, setSidebarOpen] = useState(() => !matchMedia('(max-width: 767px)').matches);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [deleting, setDeleting] = useState<Session[]>([]);
  const [greeting] = useState(() => pickWelcomeMessage());
  const [enterToSend, setEnterPreference] = useState(readEnterPreference);
  const [fontSize, setFontSize] = useChatPreference('icarus.chat.fontSize', 16);
  const [wide, setWide] = useChatPreference('icarus.chat.wide', false);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [exportError, setExportError] = useState('');
  const setEnterToSend = (value: boolean) => {
    setEnterPreference(value);
    try {
      localStorage.setItem('icarus.chat.enterToSend', String(value));
    } catch {
      /* Session only. */
    }
  };
  useEffect(() => {
    if (narrow) setSidebarOpen(false);
  }, [narrow]);
  useEffect(() => {
    if (!active) {
      setSettingsOpen(false);
      setSearchOpen(false);
      setConnectionOpen(false);
      if (narrow) setSidebarOpen(false);
    }
  }, [active, narrow]);
  useEffect(() => {
    setDeleting([]);
    setRenaming(false);
  }, [chat.workspace]);
  useEffect(() => {
    setRenaming(false);
  }, [chat.sessionId]);
  const selected = chat.sessions.find((session) => session.session_id === chat.sessionId);
  const empty = !chat.transcript.items.length && !chat.loading && !chat.submitting;
  const newChat = () => {
    if (chat.busy || chat.submitting) return;
    if (narrow) setSidebarOpen(false);
    chat.guard.requestLeave(() => {
      chat.clearDraft();
      chat.go({ workspace: chat.workspace });
    });
  };
  const setDraft = chat.setDraft;
  const compose = useCallback(
    (text: string) => {
      setDraft((current) => (current ? current + '\n' + text : text));
      requestAnimationFrame(() =>
        document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus(),
      );
    },
    [setDraft],
  );
  async function exportSession(session: Session) {
    if (!chat.gateway) return;
    setExportError('');
    try {
      let state = emptyTranscript();
      let cursor = 0;
      for (;;) {
        const history = await chat.gateway.request<History>('session.get_history', {
          workspace_path: chat.workspace,
          session_id: session.session_id,
          after_sequence: cursor,
          limit: 500,
        });
        for (const update of history.records) state = applyUpdate(state, update);
        if (!history.has_more) break;
        if (history.next_after_sequence <= cursor) throw new Error('历史读取失败');
        cursor = history.next_after_sequence;
      }
      const markdown =
        '# ' +
        titleOf(session) +
        '\n\n' +
        state.items
          .filter((item) => item.kind === 'user' || item.kind === 'assistant')
          .map((item) => `## ${item.kind === 'user' ? '你' : 'Icarus'}\n\n${item.text}`)
          .join('\n\n');
      const url = URL.createObjectURL(
        new Blob([markdown], { type: 'text/markdown;charset=utf-8' }),
      );
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download =
        Array.from(titleOf(session), (char) =>
          char.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(char) ? '_' : char,
        ).join('') + '.md';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : '导出失败');
    }
  }
  useEffect(() => {
    if (!active) return;
    const keydown = (event: KeyboardEvent) => {
      if (
        !(event.ctrlKey || event.metaKey) ||
        event.altKey ||
        settingsOpen ||
        searchOpen ||
        connectionOpen ||
        chat.guard.confirming ||
        deleting.length > 0
      )
        return;
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key.toLowerCase() === 'j') {
        event.preventDefault();
        newChat();
      }
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  });
  const connectionForm = (
    <form
      className="chat-connection"
      onSubmit={(event) => {
        event.preventDefault();
        if (!chat.path.trim() || chat.busy || chat.submitting) return;
        setConnectionOpen(false);
        setSettingsOpen(false);
        chat.connectWorkspace();
      }}
    >
      <label className="field">
        <span>工作区路径</span>
        <input
          type="text"
          aria-label="工作区路径"
          placeholder="服务端工作区的绝对路径"
          value={chat.path}
          onChange={(event) => chat.setPath(event.target.value)}
          disabled={chat.busy || chat.submitting}
        />
      </label>
      <div className="setting-row">
        <span className="chat-connection-status" role="status">
          {chat.connected ? '已连接' : chat.workspace ? '未连接' : '选择工作区开始'}
        </span>
        <button
          type="submit"
          className="btn btn-ghost"
          disabled={!chat.path.trim() || chat.busy || chat.submitting}
        >
          连接工作区
        </button>
      </div>
    </form>
  );
  return (
    <div className="chat-workspace">
      <section
        className={`dswui app chat-page ${active ? '' : 'is-management'}`}
        aria-label="Agent 对话"
        style={
          {
            '--chat-font-size': `${fontSize}px`,
            '--message-max': wide ? '1080px' : '840px',
            ...(!active ? { width: sidebarOpen && !narrow ? '261px' : '0px' } : {}),
          } as CSSProperties
        }
      >
        <DswSidebar
          chat={chat}
          open={sidebarOpen}
          narrow={narrow}
          active
          onToggle={(value) => transitionLayout(() => setSidebarOpen(value), false)}
          onNewChat={newChat}
          onSearch={() => setSearchOpen(true)}
          onSettings={() => setSettingsOpen(true)}
          onDelete={setDeleting}
          onExport={(session) => void exportSession(session)}
          managementPage={managementPage}
          managementPaths={settings.managementPaths}
          onOpenManagement={settings.onOpenManagement}
        />
        <div className="main" hidden={!active} inert={narrow && sidebarOpen}>
          <div
            className={`topbar conversation-topbar chat-conversation-heading ${empty ? 'dsw-welcome-topbar' : ''} ${sidebarOpen ? '' : 'sidebar-is-collapsed'}`}
          >
            <div className="conversation-title-wrap">
              {renaming && selected ? (
                <div className="conversation-title-editor" data-value={titleDraft || '新对话'}>
                  <input
                    className="conversation-title-input"
                    aria-label="修改对话标题"
                    autoFocus
                    maxLength={80}
                    value={titleDraft}
                    onFocus={(event) => event.target.select()}
                    onChange={(event) => setTitleDraft(event.target.value)}
                    onBlur={async () => {
                      if (
                        !titleDraft.trim() ||
                        (await chat.renameSession(selected.session_id, titleDraft))
                      )
                        setRenaming(false);
                    }}
                    onKeyDown={(event) => {
                      if (event.nativeEvent.isComposing) return;
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        event.currentTarget.blur();
                      }
                      if (event.key === 'Escape') setRenaming(false);
                    }}
                  />
                </div>
              ) : (
                <h2 className="conversation-title" title={selected ? titleOf(selected) : undefined}>
                  <button
                    className="chat-title-edit"
                    disabled={!selected || !chat.connected}
                    aria-label="修改对话标题"
                    onClick={() => {
                      if (selected) {
                        setTitleDraft(titleOf(selected));
                        setRenaming(true);
                      }
                    }}
                  >
                    {empty ? '' : selected ? titleOf(selected) : '新会话'}
                  </button>
                </h2>
              )}
            </div>
            {(chat.submitting || chat.transcript.tasks.length > 0) && (
              <span className="chat-running" role="status">
                正在执行
              </span>
            )}
            <span className="chat-connection-status" role="status">
              {chat.connected ? '已连接' : ''}
            </span>
          </div>
          {chat.error && (
            <div className="error-card chat-error" role="alert">
              <p>{chat.error}</p>
              <button className="mini-btn" onClick={chat.reconnect}>
                重新连接
              </button>
            </div>
          )}
          {exportError && (
            <p className="chat-error error-card" role="alert">
              {exportError}
            </p>
          )}
          <div className={empty ? 'welcome' : 'dsw-conversation-body'}>
            <div className="welcome-heading" hidden={!empty}>
              <span className="welcome-mark">
                <BrandMark size={34} />
              </span>
              <h1>{greeting}</h1>
            </div>
            <div className="dsw-transcript-slot" hidden={empty}>
              <ChatTranscript
                key={chat.workspace + chat.sessionId}
                transcript={chat.transcript}
                active={active}
                sessionId={chat.sessionId}
                submitting={chat.submitting}
                loading={chat.loading || Boolean(chat.workspace && !chat.connected && !chat.error)}
                onCompose={compose}
              />
            </div>
            <div className={empty ? 'welcome-composer' : 'dsw-composer-slot'}>
              <DswComposer
                chat={chat}
                enterToSend={enterToSend}
                onConnect={() => setConnectionOpen(true)}
              />
            </div>
          </div>
        </div>
        <ChatSettings
          {...settings}
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          connectionForm={connectionForm}
          enterToSend={enterToSend}
          setEnterToSend={setEnterToSend}
          fontSize={fontSize}
          setFontSize={setFontSize}
          wide={wide}
          setWide={setWide}
          gateway={chat.gateway}
          deepSearch={chat.deepSearch}
          setDeepSearch={chat.setDeepSearch}
        />
        <ChatDialog
          open={connectionOpen}
          label="切换工作区"
          onClose={() => setConnectionOpen(false)}
        >
          <div className="dialog">
            <div className="dialog-head">
              <h2>工作区</h2>
              <button
                className="icon-btn"
                aria-label="关闭工作区"
                onClick={() => setConnectionOpen(false)}
              >
                <IconClose />
              </button>
            </div>
            <div className="dialog-body">{connectionForm}</div>
          </div>
        </ChatDialog>
        <ChatDialog open={searchOpen} label="搜索对话" search onClose={() => setSearchOpen(false)}>
          <div className="search-dialog">
            <div className="search-dialog-input">
              <IconSearch />
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label="搜索会话…"
                placeholder="搜索对话标题和首条消息..."
              />
              <button onClick={() => setSearchOpen(false)} aria-label="关闭搜索">
                ×
              </button>
            </div>
            {query && (
              <div className="search-results" role="list" aria-label="搜索结果">
                {chat.sessions
                  .filter((session) =>
                    (titleOf(session) + ' ' + session.first_user_input)
                      .toLowerCase()
                      .includes(query.trim().toLowerCase()),
                  )
                  .map((session) => (
                    <button
                      className="search-result"
                      key={session.session_id}
                      aria-label={titleOf(session)}
                      onClick={() => {
                        setSearchOpen(false);
                        if (narrow) setSidebarOpen(false);
                        chat.go({ workspace: chat.workspace, session: session.session_id });
                      }}
                    >
                      <span className="search-result-icon">
                        <IconSearch />
                      </span>
                      <span className="search-result-copy">
                        <strong>{titleOf(session)}</strong>
                        <span>{session.first_user_input.slice(0, 100)}</span>
                      </span>
                    </button>
                  ))}
              </div>
            )}
          </div>
        </ChatDialog>
        <ChatDialog
          open={deleting.length > 0}
          label={deleting.length > 1 ? `删除选择的 ${deleting.length} 个对话？` : '删除这段对话？'}
          onClose={() => {
            if (!chat.busy) setDeleting([]);
          }}
        >
          <div className="confirm-modal">
            <h3>
              {deleting.length > 1 ? `删除选择的 ${deleting.length} 个对话？` : '删除这段对话？'}
            </h3>
            {chat.deleteError && <p role="alert">{chat.deleteError}</p>}
            <div className="confirm-actions">
              <button
                className="confirm-cancel"
                disabled={chat.busy}
                onClick={() => setDeleting([])}
              >
                取消
              </button>
              <button
                className="confirm-ok danger"
                disabled={chat.busy}
                onClick={async () => {
                  const deleted = await chat.deleteSessions(
                    deleting.map((session) => session.session_id),
                  );
                  setDeleting((items) =>
                    items.filter((session) => !deleted.includes(session.session_id)),
                  );
                }}
              >
                删除
              </button>
            </div>
          </div>
        </ChatDialog>
        <ChatDialog
          open={chat.guard.confirming}
          label="放弃未发送的消息？"
          onClose={chat.guard.keepEditing}
        >
          <div className="confirm-modal">
            <h3>放弃未发送的消息？</h3>
            <div className="confirm-actions">
              <button className="confirm-cancel" onClick={chat.guard.keepEditing}>
                继续编辑
              </button>
              <button
                className="confirm-ok danger"
                disabled={chat.busy}
                onClick={chat.discardDraft}
              >
                放弃
              </button>
            </div>
          </div>
        </ChatDialog>
      </section>
      <div
        className={`chat-management-root ${sidebarOpen && !narrow ? '' : 'sidebar-is-collapsed'}`}
        hidden={active}
        inert={narrow && sidebarOpen}
      >
        {managementContent}
      </div>
    </div>
  );
}
