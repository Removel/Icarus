import { useEffect, useRef, useState } from 'react';
import { Plus, Pause, Play, X, ChevronRight, Info } from 'lucide-react';
import {
  Button,
  Checkbox,
  Select,
  Modal,
  Toast,
  PageHeading,
  Status,
  Tabs,
  SearchField,
  Field,
  EmptyState,
  LoadingIndicator,
  SortButton,
  navigate,
} from '@icarus/ui';
import { isExpired, memoryDate, type MemoryEntry } from './types';
import * as api from './mem0';
import useMemories from './useMemories';
import MemoryDetail, { type MemoryChanges } from './MemoryDetail';
import MemoryCreate from './MemoryCreate';
import { scopeName, validScope } from './context';
import './memory.css';

const stateName = (memory: MemoryEntry) =>
  memory.expiration_date === '1970-01-01' ? '已停用' : isExpired(memory) ? '已过期' : '生效中';

export default function MemoryApp({
  active,
  hash,
  workspace = '',
}: {
  active: boolean;
  hash: string;
  workspace?: string;
}) {
  const section = hash.split('?')[0].split('/')[2];
  const tab = ['all', 'active', 'expired'].includes(section) ? section : 'all';
  const selectedId = new URLSearchParams(hash.split('?')[1]).get('entry');
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [operationError, setOperationError] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [scope, setScope] = useState('all');
  const [user, setUser] = useState('all');
  const [descending, setDescending] = useState(true);
  const [selecting, setSelecting] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [create, setCreate] = useState(false);
  const [createContent, setCreateContent] = useState('');
  const [about, setAbout] = useState(false);
  const boardRef = useRef<HTMLElement>(null);
  const entryTrigger = useRef<HTMLButtonElement | null>(null);
  const listScroll = useRef(0);
  const restoreBulkFocus = useRef(false);
  const pageSize = api.pageSize;
  const filterKey = JSON.stringify([search.trim(), category, scope, user, tab, descending]);
  const [pagination, setPagination] = useState({ key: filterKey, page: 1 });
  const requestedPage = pagination.key === filterKey ? pagination.page : 1;
  useEffect(() => {
    setPagination((current) => (current.key === filterKey ? current : { key: filterKey, page: 1 }));
  }, [filterKey]);
  const query = new URLSearchParams({
    state: tab,
    query: search.trim(),
    descending: String(descending),
    page: String(requestedPage),
    page_size: String(pageSize),
  });
  if (category !== 'all') query.set('category', category);
  if (scope !== 'all') query.set('run_id', scope);
  if (user !== 'all') query.set('user_id', user);
  const data = useMemories(active, selectedId, query.toString());
  const { memories, selected } = data;
  useEffect(() => {
    if (!restoreBulkFocus.current || selecting || data.loading || busy) return;
    const frame = requestAnimationFrame(() => {
      const trigger = document.querySelector<HTMLButtonElement>('.memory-bulk-trigger');
      restoreBulkFocus.current = false;
      (trigger && !trigger.disabled ? trigger : boardRef.current)?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [selecting, data.loading, busy]);

  function changePage(page: number) {
    exitSelection();
    setPagination({ key: filterKey, page });
    boardRef.current?.focus({ preventScroll: true });
  }

  function go(nextTab = tab, id?: string, replace = false) {
    navigate(`#/memory/${nextTab}${id ? `?entry=${encodeURIComponent(id)}` : ''}`, replace);
  }

  useEffect(() => {
    if (active && selectedId && data.missingId === selectedId) go(tab, undefined, true);
  }, [active, selectedId, data.missingId, tab]);

  useEffect(() => {
    if (!active) {
      setCreate(false);
      setAbout(false);
    }
  }, [active]);

  useEffect(() => {
    setChecked([]);
    setSelecting(false);
  }, [search, category, scope, user, tab, active, descending]);
  useEffect(() => {
    if (selecting)
      document
        .querySelector<HTMLInputElement>('.selection-toolbar input')
        ?.focus({ preventScroll: true });
  }, [selecting]);

  function exitSelection(restoreFocus = false) {
    restoreBulkFocus.current = restoreFocus;
    setChecked([]);
    setSelecting(false);
  }

  const activeCount = data.counts.active;
  const categories = data.categories;
  const users = data.users;
  const filtered = memories
    .filter(
      (item) =>
        (tab === 'all' || (tab === 'active' ? !isExpired(item) : isExpired(item))) &&
        (category === 'all' || item.metadata.category === category) &&
        (scope === 'all' || item.run_id === scope) &&
        (user === 'all' || item.user_id === user) &&
        `${item.memory} ${item.metadata.category} ${item.user_id}`
          .toLowerCase()
          .includes(search.trim().toLowerCase()),
    )
    .sort((a, b) => (descending ? -1 : 1) * (Date.parse(a.updated_at) - Date.parse(b.updated_at)));
  const checkedItems = filtered.filter((item) => checked.includes(item.id));
  const pauseCount = checkedItems.filter((item) => !isExpired(item)).length;
  const restoreCount = checkedItems.length - pauseCount;
  const filterCount = [category, scope, user].filter((value) => value !== 'all').length;

  function clearFilters() {
    setSearch('');
    setCategory('all');
    setScope('all');
    setUser('all');
    setChecked([]);
  }

  function openDetail(item: MemoryEntry, trigger: HTMLButtonElement) {
    entryTrigger.current = trigger;
    listScroll.current = document.querySelector('.shell-main')?.scrollTop ?? 0;
    go(tab, item.id);
  }

  function restoreList() {
    if (!window.location.hash.startsWith('#/memory/')) return;
    const scroll = document.querySelector('.shell-main');
    if (scroll) scroll.scrollTop = listScroll.current;
    const trigger = entryTrigger.current;
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    else boardRef.current?.focus({ preventScroll: true });
  }

  async function syncItem(id: string) {
    const item = await api.get(id);
    if (!item) throw new Error('操作后无法读取记忆，请刷新列表确认结果。');
    data.upsert({ ...item, history: await api.history(id) });
  }

  async function saveEdit(id: string, changes: MemoryChanges) {
    await api.update(id, {
      text: changes.memory,
      metadata: changes.metadata,
      expiration_date: changes.expiration_date,
    });
    await syncItem(id);
    data.refresh();
    Toast.success('已保存修改');
  }

  function openCreate(content = '') {
    exitSelection();
    setCreateContent(content);
    setCreate(true);
  }

  function created(id: string) {
    setCreate(false);
    data.refresh();
    clearFilters();
    setPagination({
      key: JSON.stringify(['', 'all', 'all', 'all', 'all', descending]),
      page: 1,
    });
    go('all', id);
    Toast.success('已添加记忆');
  }

  async function changeStatus(items: MemoryEntry[], restore: boolean) {
    if (working.current) return;
    const ids = items
      .filter((item) => (restore ? isExpired(item) : !isExpired(item)))
      .map((item) => item.id);
    if (!ids.length) return;
    working.current = true;
    setBusy(true);
    setOperationError('');
    const failures: string[] = [];
    for (const id of ids) {
      try {
        await api.update(id, { expiration_date: restore ? null : '1970-01-01' });
        await syncItem(id);
      } catch (error) {
        failures.push(`${id}：${api.message(error)}`);
      }
    }
    working.current = false;
    setBusy(false);
    exitSelection(selecting);
    data.refresh();
    if (failures.length)
      setOperationError(
        `已完成 ${ids.length - failures.length} 条，失败 ${failures.length} 条。${failures.join('；')}`,
      );
    else
      Toast.success(
        restore ? `已将 ${ids.length} 条记忆恢复为长期有效` : `已暂停 ${ids.length} 条记忆`,
      );
  }

  return (
    <div
      className="page memory-page"
      onKeyDown={(event) => {
        if (
          selecting &&
          event.key === 'Escape' &&
          (event.target as HTMLElement).closest('.selection-toolbar, .memory-board')
        ) {
          event.preventDefault();
          exitSelection(true);
        }
      }}
    >
      {(data.error || operationError || data.detailError) && (
        <div role="alert" className="knowledge-error">
          <p>{operationError || data.error || data.detailError}</p>
          <Button
            disabled={busy}
            onClick={() => {
              setOperationError('');
              data.refresh();
            }}
          >
            重试加载
          </Button>
        </div>
      )}
      {data.truncated && (
        <p role="status">当前查询最多覆盖 1000 条记忆，搜索、排序和筛选仅作用于这个范围。</p>
      )}
      <PageHeading
        title="记忆"
        hideTitle
        context={
          <div className="memory-context">
            <Field label="所属用户">
              <Select
                aria-label="筛选用户"
                value={user}
                onChange={(value) => setUser(String(value))}
                optionList={[
                  { value: 'all', label: '所有用户' },
                  ...users.map((name) => ({ value: name, label: name })),
                ]}
              />
            </Field>
            <Field label="作用范围">
              <Select
                aria-label="筛选作用范围"
                value={scope}
                onChange={(value) => setScope(String(value))}
                optionList={[
                  { value: 'all', label: '所有范围' },
                  ...[...new Set(['global', ...data.scopes])].map((value) => ({
                    value,
                    label: scopeName(value),
                  })),
                ]}
              />
            </Field>
          </div>
        }
      >
        <Button theme="solid" icon={<Plus size={16} />} onClick={() => openCreate()}>
          添加记忆
        </Button>
      </PageHeading>
      <div className="memory-state-nav">
        <Tabs
          value={tab}
          onChange={(id) => go(id)}
          items={[
            { id: 'all', label: '全部', count: data.counts.all },
            { id: 'active', label: '生效中', count: activeCount },
            { id: 'expired', label: '已失效', count: data.counts.expired },
          ]}
        />
        <Button
          className="icon-button"
          type="tertiary"
          theme="borderless"
          icon={<Info size={17} />}
          aria-label="记忆规则"
          title="记忆规则"
          onClick={() => setAbout(true)}
        />
      </div>
      {selecting ? (
        <div className="selection-toolbar" role="region" aria-label="批量操作">
          <Checkbox
            checked={filtered.length > 0 && checkedItems.length === filtered.length}
            indeterminate={checkedItems.length > 0 && checkedItems.length < filtered.length}
            onChange={() =>
              setChecked(
                checkedItems.length === filtered.length ? [] : filtered.map((item) => item.id),
              )
            }
          >
            全选当前结果
          </Checkbox>
          <span className="selection-count" role="status">
            已选 <strong>{checkedItems.length}</strong> 条
          </span>
          <div className="selection-actions">
            {pauseCount > 0 && (
              <Button
                type="tertiary"
                theme="borderless"
                disabled={busy}
                icon={<Pause size={14} />}
                onClick={() => changeStatus(checkedItems, false)}
              >
                暂停使用（{pauseCount}）
              </Button>
            )}
            {restoreCount > 0 && (
              <Button
                type="tertiary"
                theme="borderless"
                disabled={busy}
                icon={<Play size={14} />}
                onClick={() => changeStatus(checkedItems, true)}
              >
                恢复使用（{restoreCount}）
              </Button>
            )}
          </div>
          <Button
            className="selection-cancel"
            type="tertiary"
            theme="borderless"
            onClick={() => exitSelection(true)}
          >
            取消多选
          </Button>
        </div>
      ) : (
        <div className="toolbar">
          <SearchField value={search} onChange={setSearch} placeholder="搜索记忆…" />
          <Select
            aria-label="筛选记忆分类"
            value={category}
            onChange={(value) => setCategory(String(value))}
            optionList={[
              { value: 'all', label: '全部分类' },
              ...categories.map((name) => ({ value: name, label: name })),
            ]}
          />
          <span className="toolbar-spacer" />
          <SortButton descending={descending} onClick={() => setDescending((value) => !value)} />
          <Button
            className="memory-bulk-trigger"
            type="tertiary"
            theme="borderless"
            disabled={busy || data.loading || data.page_size !== pageSize || !filtered.length}
            onClick={() => {
              setChecked([]);
              setSelecting(true);
            }}
          >
            多选
          </Button>
        </div>
      )}
      {(filterCount > 0 || search) && (
        <div className="filter-summary">
          <span>
            {data.total} 条结果 · 当前状态共{' '}
            {tab === 'all' ? data.counts.all : tab === 'active' ? activeCount : data.counts.expired}{' '}
            条
          </span>
          {search && (
            <button className="filter-chip" aria-label="移除搜索条件" onClick={() => setSearch('')}>
              {search}
              <X size={12} />
            </button>
          )}
          {category !== 'all' && (
            <button
              className="filter-chip"
              aria-label="移除分类筛选"
              onClick={() => setCategory('all')}
            >
              {category}
              <X size={12} />
            </button>
          )}
          {scope !== 'all' && (
            <button
              className="filter-chip"
              aria-label="移除范围筛选"
              onClick={() => setScope('all')}
            >
              {scopeName(scope)}
              <X size={12} />
            </button>
          )}
          {user !== 'all' && (
            <button
              className="filter-chip"
              aria-label="移除用户筛选"
              onClick={() => setUser('all')}
            >
              {user}
              <X size={12} />
            </button>
          )}
          <button className="link-button" onClick={clearFilters}>
            清除筛选
            <X size={12} />
          </button>
        </div>
      )}
      <section
        ref={boardRef}
        tabIndex={-1}
        className="memory-board"
        aria-label="记忆列表"
        aria-busy={data.loading}
      >
        {data.loading && !filtered.length && <LoadingIndicator label="正在加载记忆" />}
        {filtered.length ? (
          <div className="memory-list">
            {filtered.map((item) => {
              const content = (
                <>
                  <span className="memory-entry-copy">
                    <span className="memory-content">{item.memory}</span>
                    <span className="memory-meta">
                      <Status
                        tone={
                          isExpired(item) ? 'gray' : validScope(item.run_id) ? 'green' : 'amber'
                        }
                      >
                        {isExpired(item)
                          ? stateName(item)
                          : validScope(item.run_id)
                            ? '生效中'
                            : '范围待修正'}
                      </Status>
                      <span className="memory-category">{item.metadata.category}</span>
                      <span>{item.user_id}</span>
                      <span
                        title={scopeName(item.run_id)}
                        className={`memory-scope ${item.run_id === 'global' ? 'is-global' : ''}`}
                      >
                        {scopeName(item.run_id)}
                      </span>
                    </span>
                  </span>
                  <time
                    dateTime={item.updated_at}
                    title={new Date(item.updated_at).toLocaleString('zh-CN')}
                  >
                    {memoryDate(item.updated_at)}
                  </time>
                </>
              );
              return (
                <article
                  data-memory-id={item.id}
                  className={`memory-entry ${checked.includes(item.id) ? 'is-checked' : ''} ${isExpired(item) ? 'is-inactive' : ''}`}
                  key={item.id}
                  style={{ viewTransitionName: `memory-${CSS.escape(item.id)}` }}
                >
                  {selecting ? (
                    <Checkbox
                      className="memory-entry-select"
                      checked={checked.includes(item.id)}
                      onChange={() =>
                        setChecked((ids) =>
                          ids.includes(item.id)
                            ? ids.filter((id) => id !== item.id)
                            : [...ids, item.id],
                        )
                      }
                    >
                      <span className="visually-hidden">选择记忆：</span>
                      <span className="memory-entry-selection-content">{content}</span>
                    </Checkbox>
                  ) : (
                    <button
                      className="memory-entry-open"
                      aria-label={`查看记忆：${item.memory}`}
                      onClick={(event) => openDetail(item, event.currentTarget)}
                    >
                      {content}
                      <ChevronRight size={16} />
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        ) : !data.loading && !data.error ? (
          <EmptyState title={data.counts.all ? '没有找到匹配的内容' : '还没有记忆'}>
            {data.counts.all ? (
              <Button onClick={clearFilters}>清除筛选</Button>
            ) : (
              <Button icon={<Plus size={15} />} onClick={() => openCreate()}>
                记录第一条记忆
              </Button>
            )}
          </EmptyState>
        ) : null}
      </section>
      {data.total > 0 && (
        <nav className="memory-pagination" aria-label="记忆分页">
          <span role="status">
            共 {data.total} 条 · 每页 {data.page_size} 条 · 第 {data.page} /{' '}
            {Math.ceil(data.total / data.page_size)} 页
          </span>
          <Button
            disabled={data.loading || busy || data.page <= 1}
            onClick={() => changePage(data.page - 1)}
          >
            上一页
          </Button>
          <Button
            disabled={data.loading || busy || data.page * data.page_size >= data.total}
            onClick={() => changePage(data.page + 1)}
          >
            下一页
          </Button>
        </nav>
      )}
      <MemoryDetail
        onRecreate={(item) => {
          go(tab);
          openCreate(item.memory);
        }}
        item={selected}
        active={active}
        loading={data.detailLoading || busy}
        loadError={data.detailError}
        onClose={() => go(tab)}
        onAfterClose={restoreList}
        onSave={saveEdit}
        onChangeStatus={(item) => changeStatus([item], isExpired(item))}
        onDelete={async (item) => {
          await api.remove(item.id);
          data.removed(item.id);
          data.refresh();
          setChecked((ids) => ids.filter((id) => id !== item.id));
          go(tab, undefined, true);
          Toast.success('已删除记忆');
        }}
      />
      {create && (
        <MemoryCreate
          active={active}
          workspace={workspace}
          content={createContent}
          onClose={() => setCreate(false)}
          onCreated={created}
        />
      )}
      <Modal
        title="记忆规则"
        visible={about}
        onCancel={() => setAbout(false)}
        footer={
          <Button theme="solid" onClick={() => setAbout(false)}>
            知道了
          </Button>
        }
      >
        <dl className="detail-properties">
          <div>
            <dt>全局</dt>
            <dd>在你的所有工作区中使用。</dd>
          </div>
          <div>
            <dt>工作区</dt>
            <dd>只在指定工作区中使用。</dd>
          </div>
          <div>
            <dt>暂停 / 过期</dt>
            <dd>不再用于对话，内容和修改记录会保留。</dd>
          </div>
          <div>
            <dt>恢复</dt>
            <dd>重新启用，并清除原有效期。需要限时使用时，可在详情中设置。</dd>
          </div>
        </dl>
        <p className="field-hint">记忆会按原文保存。对话时会参考相关记忆，不一定每次都使用。</p>
      </Modal>
    </div>
  );
}
