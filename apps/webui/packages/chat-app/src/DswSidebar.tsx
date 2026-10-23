// dswui Sidebar/Header/SessionItem/Footer markup adapted to Gateway sessions.
import { useEffect, useRef, useState } from 'react';
import { BrandMark } from '@icarus/ui';
import type useChatSession from './useChatSession';
import type { Session } from './gateway';
import useChatPreference from './useChatPreference';
import { BookOpen, Brain, Download, Pencil, Pin } from 'lucide-react';
import type { ManagementPage } from './ChatSettings';
import ChatDialog from './ChatDialog';
import {
  IconCheck,
  IconClose,
  IconEmptyHistory,
  IconMore,
  IconMultiSelect,
  IconNewChat,
  IconSearch,
  IconSettings,
  IconSidebar,
  IconTrash,
} from './dswui/Icons';

export const titleOf = (session: Session) => {
  if (session.title) return session.title;
  const firstLine = session.first_user_input?.trim().split(/\r?\n/)[0] ?? '';
  const preview = firstLine.replace(/\s+/g, ' ');
  return preview ? Array.from(preview).slice(0, 12).join('') : '新会话';
};

function groupSessions(sessions: Session[], pinned: string[]) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets: Record<string, Session[]> = {
    置顶: [],
    今天: [],
    昨天: [],
    '7 天内': [],
    '30 天内': [],
    更早: [],
    对话: [],
  };
  for (const session of sessions) {
    if (pinned.includes(session.session_id)) {
      buckets.置顶.push(session);
      continue;
    }
    const timestamp = Date.parse(session.updated_at || session.created_at || '');
    const days = (today.getTime() - timestamp) / 86400000;
    const label = !Number.isFinite(timestamp)
      ? '对话'
      : days <= 0
        ? '今天'
        : days <= 1
          ? '昨天'
          : days <= 7
            ? '7 天内'
            : days <= 30
              ? '30 天内'
              : '更早';
    buckets[label].push(session);
  }
  return Object.entries(buckets).filter(([, items]) => items.length);
}

export default function DswSidebar({
  chat,
  open,
  narrow,
  active,
  onToggle,
  onNewChat,
  onSearch,
  onSettings,
  onDelete,
  onExport,
  managementPage,
  managementPaths,
  onOpenManagement,
}: {
  chat: ReturnType<typeof useChatSession>;
  open: boolean;
  narrow: boolean;
  active: boolean;
  onToggle: (value: boolean) => void;
  onNewChat: () => void;
  onSearch: () => void;
  onSettings: () => void;
  onDelete: (sessions: Session[]) => void;
  onExport: (session: Session) => void;
  managementPage?: ManagementPage;
  managementPaths: Record<ManagementPage, string>;
  onOpenManagement: (page: ManagementPage) => void;
}) {
  const list = useRef<HTMLElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const [menu, setMenu] = useState('');
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const menuTrigger = useRef<HTMLElement | null>(null);
  const collapsedTrigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const [pins, setPins] = useChatPreference<Record<string, string[]>>('icarus.chat.pins', {});
  const pinned = pins[chat.workspace] ?? [];
  const groups = groupSessions(chat.sessions, pinned);
  const [renaming, setRenaming] = useState('');
  const [titleDraft, setTitleDraft] = useState('');
  const [multi, setMulti] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [pinConfirm, setPinConfirm] = useState(false);
  const selectedSessions = chat.sessions.filter((session) =>
    selectedIds.includes(session.session_id),
  );
  const allPinned =
    selectedSessions.length > 0 &&
    selectedSessions.every((session) => pinned.includes(session.session_id));
  const exitMulti = () => {
    setMulti(false);
    setSelectedIds([]);
    setPinConfirm(false);
  };
  const toggleSelected = (id: string) =>
    setSelectedIds((ids) =>
      ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id],
    );
  useEffect(() => {
    setMulti(false);
    setSelectedIds([]);
    setPinConfirm(false);
    setRenaming('');
    setMenu('');
  }, [chat.workspace]);
  useEffect(() => {
    if (!multi) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('dialog[open]')) exitMulti();
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [multi]);
  useEffect(() => {
    if (selectedIds.length && !selectedSessions.length && !chat.busy) exitMulti();
  }, [selectedIds.length, selectedSessions.length, chat.busy]);
  const menuSession = chat.sessions.find((session) => session.session_id === menu);
  const saveTitle = async () => {
    if (!renaming) return;
    if (!titleDraft.trim() || (await chat.renameSession(renaming, titleDraft))) setRenaming('');
  };
  useEffect(() => {
    if (!active || !open) return;
    list.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest' });
  }, [active, open, chat.sessionId, chat.sessions.length]);
  useEffect(() => {
    if (narrow && open) sidebar.current?.querySelector<HTMLButtonElement>('button')?.focus();
    else if (restoreFocus.current) {
      collapsedTrigger.current?.focus();
      restoreFocus.current = false;
    }
  }, [narrow, open]);
  function close() {
    restoreFocus.current = true;
    onToggle(false);
  }
  function popup(id: string, trigger: HTMLButtonElement) {
    menuTrigger.current = trigger;
    const rect = trigger.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(rect.left, innerWidth - 190)),
      top:
        id === 'profile'
          ? rect.top - 56
          : Math.max(8, Math.min(rect.bottom + 4, innerHeight - 200)),
    });
    setMenu(id);
  }
  function closeMenu() {
    setMenu('');
    menuTrigger.current?.focus();
  }
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu('');
    window.addEventListener('resize', close);
    return () => window.removeEventListener('resize', close);
  }, [menu]);
  const busy = chat.busy || chat.submitting;
  return (
    <>
      <aside
        ref={sidebar}
        className={`sidebar chat-sessions ${open ? '' : 'collapsed'}`}
        aria-label="会话管理"
        inert={!open || !active}
        onKeyDown={(event) => {
          if (!narrow || !open) return;
          if (event.key === 'Escape') {
            event.preventDefault();
            if (multi) exitMulti();
            else close();
          }
          if (event.key === 'Tab') {
            const elements = [
              ...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
            ];
            if (event.shiftKey && document.activeElement === elements[0]) {
              event.preventDefault();
              elements.at(-1)?.focus();
            } else if (!event.shiftKey && document.activeElement === elements.at(-1)) {
              event.preventDefault();
              elements[0]?.focus();
            }
          }
        }}
      >
        <div className="sidebar-top">
          <div className="brand-row">
            <button
              className="brand"
              onClick={() => {
                exitMulti();
                onNewChat();
              }}
              title="返回主页"
              aria-label="返回主页"
            >
              <BrandMark size={24} />
              <img className="icarus-wordmark" src="/icarus-wordmark.svg" alt="Icarus" />
            </button>
            <div className="brand-actions">
              {!multi && (
                <button
                  className="icon-btn"
                  onClick={onSearch}
                  title="搜索对话 Ctrl K"
                  aria-label="搜索对话"
                >
                  <IconSearch />
                </button>
              )}
              <button
                className="icon-btn"
                onClick={close}
                title="收起边栏"
                aria-label="收起会话列表"
              >
                <IconSidebar width={16} height={16} />
              </button>
            </div>
          </div>
          {multi ? (
            <div className="session-batch-bar">
              <span className="count">
                {selectedSessions.length ? `已选择 ${selectedSessions.length} 个对话` : '选择对话'}
              </span>
              <button onClick={exitMulti} aria-label="退出多选" title="退出多选" disabled={busy}>
                <IconClose width={12} height={12} />
              </button>
            </div>
          ) : (
            <button className="new-chat" onClick={onNewChat} disabled={busy} aria-label="新建会话">
              <IconNewChat width={16} height={16} />
              <span>开启新对话</span>
            </button>
          )}
          <nav className="chat-management-links" aria-label="工作台入口">
            {(
              [
                { id: 'knowledge', label: '知识库', icon: BookOpen },
                { id: 'memory', label: '记忆', icon: Brain },
              ] as const
            ).map((item) => (
              <a
                className="new-chat"
                key={item.id}
                href={managementPaths[item.id]}
                aria-current={managementPage === item.id ? 'page' : undefined}
                onClick={(event) => {
                  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  if (narrow) close();
                  onOpenManagement(item.id);
                }}
              >
                <item.icon size={16} />
                <span>{item.label}</span>
              </a>
            ))}
          </nav>
        </div>
        <div className={`session-region ${!chat.sessions.length ? 'empty' : ''}`}>
          <nav
            ref={list}
            className={`session-list chat-session-list ${!chat.sessions.length ? 'empty' : ''}`}
            aria-label="会话列表"
          >
            {groups.map(([label, sessions], index) => (
              <div key={label}>
                <div className="session-group-title">
                  <span>{label}</span>
                  {index === 0 &&
                    (!multi ? (
                      <button
                        className="session-batch-entry"
                        aria-label="多选"
                        title="多选"
                        disabled={busy}
                        onClick={() => {
                          setRenaming('');
                          setMulti(true);
                        }}
                      >
                        <IconMultiSelect />
                      </button>
                    ) : (
                      <button
                        className="session-select-all"
                        disabled={busy}
                        onClick={() =>
                          setSelectedIds(
                            selectedSessions.length === chat.sessions.length
                              ? []
                              : chat.sessions.map((session) => session.session_id),
                          )
                        }
                      >
                        {selectedSessions.length === chat.sessions.length ? '取消全选' : '全选'}
                      </button>
                    ))}
                </div>
                {sessions.map((session) => (
                  <div
                    className={`session-item ${!managementPage && chat.sessionId === session.session_id ? 'active' : ''} ${renaming === session.session_id ? 'is-renaming' : ''}`}
                    key={session.session_id}
                    title={titleOf(session)}
                  >
                    {multi && (
                      <button
                        className={`session-check ${selectedIds.includes(session.session_id) ? 'on' : ''}`}
                        role="checkbox"
                        aria-checked={selectedIds.includes(session.session_id)}
                        aria-label={`选择对话：${titleOf(session)}`}
                        disabled={busy}
                        onClick={() => toggleSelected(session.session_id)}
                      >
                        <IconCheck width={12} height={12} />
                      </button>
                    )}
                    {renaming === session.session_id ? (
                      <input
                        className="rename"
                        aria-label="会话标题"
                        autoFocus
                        maxLength={80}
                        value={titleDraft}
                        onFocus={(event) => event.target.select()}
                        onChange={(event) => setTitleDraft(event.target.value)}
                        onBlur={() => void saveTitle()}
                        onKeyDown={(event) => {
                          if (event.nativeEvent.isComposing) return;
                          if (event.key === 'Enter') {
                            event.preventDefault();
                            event.currentTarget.blur();
                          }
                          if (event.key === 'Escape') {
                            event.preventDefault();
                            setRenaming('');
                          }
                        }}
                      />
                    ) : (
                      <button
                        className="title chat-session"
                        disabled={busy}
                        aria-current={chat.sessionId === session.session_id ? 'page' : undefined}
                        onClick={() => {
                          if (multi) {
                            toggleSelected(session.session_id);
                            return;
                          }
                          if (narrow) close();
                          chat.go({ workspace: chat.workspace, session: session.session_id });
                        }}
                      >
                        {titleOf(session)}
                      </button>
                    )}
                    {!multi && pinned.includes(session.session_id) && (
                      <Pin className="pin-dot" size={12} aria-label="已置顶" />
                    )}
                    {!multi && (
                      <span className="row-actions">
                        <button
                          className="icon-btn"
                          aria-label={`会话操作：${titleOf(session)}`}
                          aria-haspopup="menu"
                          aria-expanded={menu === session.session_id}
                          disabled={busy}
                          onClick={(event) => popup(session.session_id, event.currentTarget)}
                        >
                          <IconMore width={18} height={18} />
                        </button>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {!chat.sessions.length && (
              <div className="session-empty">
                <IconEmptyHistory />
                <span>暂无历史对话</span>
              </div>
            )}
          </nav>
        </div>
        {multi ? (
          <div className="session-batch-actions">
            <button disabled={!selectedSessions.length || busy} onClick={() => setPinConfirm(true)}>
              <Pin size={16} />
              {allPinned ? '取消置顶' : '置顶'}
            </button>
            <span className="batch-divider" />
            <button
              className="danger"
              disabled={
                !selectedSessions.length ||
                !chat.connected ||
                busy ||
                selectedSessions.some(
                  (session) =>
                    session.session_id === chat.sessionId &&
                    (chat.transcript.tasks.length > 0 ||
                      Boolean(chat.draft) ||
                      chat.queue.length > 0),
                )
              }
              onClick={() => onDelete(selectedSessions)}
            >
              <IconTrash width={16} height={16} />
              删除
            </button>
          </div>
        ) : (
          <div className="sidebar-foot">
            <button
              className="profile-trigger"
              onClick={(event) => popup('profile', event.currentTarget)}
              aria-label="账户菜单"
              aria-haspopup="menu"
              aria-expanded={menu === 'profile'}
            >
              <span className="avatar">I</span>
              <span className="who" title={chat.workspace || 'Icarus'}>
                {chat.workspace.split(/[\\/]/).filter(Boolean).pop() || 'Icarus'}
              </span>
              <IconMore className="profile-more" />
            </button>
          </div>
        )}
      </aside>
      {!open && (
        <div className="collapsed-nav">
          <button
            className="collapsed-brand"
            onClick={onNewChat}
            title="返回主页"
            aria-label="返回主页"
          >
            <BrandMark size={27} />
          </button>
          <div className="collapsed-actions">
            <button
              ref={collapsedTrigger}
              className="icon-btn"
              onClick={() => onToggle(true)}
              title="打开边栏"
              aria-label={narrow ? '打开会话列表' : '展开会话列表'}
            >
              <IconSidebar />
            </button>
            <button className="icon-btn" onClick={onSearch} title="搜索对话" aria-label="搜索对话">
              <IconSearch />
            </button>
            <button
              className="icon-btn"
              onClick={onNewChat}
              title="开启新对话"
              aria-label="新建会话"
              disabled={busy}
            >
              <IconNewChat width={16} height={16} />
            </button>
          </div>
        </div>
      )}
      {open && narrow && <div className="side-mask mobile-only" onClick={close} />}
      {menu && (
        <>
          <div className="popup-menu-mask" onClick={closeMenu} />
          <div
            className="popup-menu"
            style={position}
            role="menu"
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeMenu();
              }
            }}
          >
            {menu === 'profile' ? (
              <button
                role="menuitem"
                autoFocus
                onClick={() => {
                  closeMenu();
                  if (narrow) close();
                  requestAnimationFrame(onSettings);
                }}
                aria-label="打开设置"
              >
                <IconSettings />
                系统设置
              </button>
            ) : menuSession ? (
              <>
                <button
                  role="menuitem"
                  autoFocus
                  onClick={() => {
                    closeMenu();
                    setTitleDraft(titleOf(menuSession));
                    setRenaming(menuSession.session_id);
                  }}
                >
                  <Pencil size={16} />
                  重命名
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    setPins({
                      ...pins,
                      [chat.workspace]: pinned.includes(menu)
                        ? pinned.filter((id) => id !== menu)
                        : [...pinned, menu],
                    });
                    closeMenu();
                  }}
                >
                  <Pin size={16} />
                  {pinned.includes(menu) ? '取消置顶' : '置顶'}
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    closeMenu();
                    onExport(menuSession);
                  }}
                >
                  <Download size={16} />
                  导出 Markdown
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    closeMenu();
                    setRenaming('');
                    setMulti(true);
                    setSelectedIds([menuSession.session_id]);
                  }}
                >
                  <IconMultiSelect />
                  多选
                </button>
                <button
                  role="menuitem"
                  className="danger"
                  disabled={
                    !chat.connected ||
                    busy ||
                    (menu === chat.sessionId &&
                      (chat.transcript.tasks.length > 0 ||
                        Boolean(chat.draft) ||
                        chat.queue.length > 0))
                  }
                  aria-label={`删除会话：${titleOf(menuSession)}`}
                  onClick={() => {
                    const session = chat.sessions.find((s) => s.session_id === menu);
                    closeMenu();
                    if (narrow) close();
                    if (session) requestAnimationFrame(() => onDelete([session]));
                  }}
                >
                  <IconTrash />
                  删除
                </button>
              </>
            ) : null}
          </div>
        </>
      )}
      <ChatDialog
        open={pinConfirm}
        label={`将选择的 ${selectedSessions.length} 个对话${allPinned ? '取消置顶' : '置顶'}？`}
        onClose={() => setPinConfirm(false)}
      >
        <div className="confirm-modal">
          <h3>
            将选择的 {selectedSessions.length} 个对话{allPinned ? '取消置顶' : '置顶'}？
          </h3>
          <div className="confirm-actions">
            <button className="confirm-cancel" onClick={() => setPinConfirm(false)}>
              取消
            </button>
            <button
              className="confirm-ok"
              onClick={() => {
                const ids = selectedSessions.map((session) => session.session_id);
                setPins({
                  ...pins,
                  [chat.workspace]: allPinned
                    ? pinned.filter((id) => !ids.includes(id))
                    : [...new Set([...pinned, ...ids])],
                });
                exitMulti();
              }}
            >
              {allPinned ? '取消置顶' : '置顶'}
            </button>
          </div>
        </div>
      </ChatDialog>
    </>
  );
}
