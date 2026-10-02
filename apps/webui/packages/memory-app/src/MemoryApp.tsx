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
  const data = useMemories(active, selectedId);
  const { memories, selected } = data;
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
  }, [search, category, scope, user, tab, active]);
  useEffect(() => {
    if (selecting)
      document
        .querySelector<HTMLInputElement>('.selection-toolbar input')
        ?.focus({ preventScroll: true });
  }, [selecting]);

  function exitSelection(restoreFocus = false) {
    setChecked([]);
    setSelecting(false);
    if (restoreFocus)
      requestAnimationFrame(() => {
        const trigger = document.querySelector<HTMLButtonElement>('.memory-bulk-trigger');
        (trigger && !trigger.disabled ? trigger : boardRef.current)?.focus({ preventScroll: true });
      });
  }

  const activeCount = memories.filter((item) => !isExpired(item)).length;
  const categories = [...new Set(memories.map((item) => item.metadata.category))].filter((name) =>
    memories.some((item) => item.metadata.category === name),
  );
  const users = [...new Set(memories.map((item) => item.user_id))];
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
      {data.loading && <p role="status">正在加载记忆…</p>}
      {memories.length >= api.listLimit && (
        <p role="status">
          当前最多展示 {api.listLimit} 条记忆，搜索、排序和筛选仅作用于已加载列表。
        </p>
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
                  ...[...new Set(['global', ...memories.map((item) => item.run_id)])].map(
                    (value) => ({ value, label: scopeName(value) }),
                  ),
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
            { id: 'all', label: '全部', count: memories.length },
            { id: 'active', label: '生效中', count: activeCount },
            { id: 'expired', label: '已失效', count: memories.length - activeCount },
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
            disabled={busy || !filtered.length}
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
            {filtered.length} 条结果 · 当前状态共{' '}
            {tab === 'all'
              ? memories.length
              : tab === 'active'
                ? activeCount
                : memories.length - activeCount}{' '}
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
      <section ref={boardRef} tabIndex={-1} className="memory-board" aria-label="记忆列表">
        {filtered.length ? (
          <div className="memory-list">
            {filtered.map((item) => {
              const content = (
                <>
                  <span className="memory-entry-copy">
                    <span className="memory-content">{item.memory}</span>
                    <span className="memory-meta">
                      <span className="memory-category">{item.metadata.category}</span>
                      <span>{item.user_id}</span>
                      <span
                        className={`memory-scope ${item.run_id === 'global' ? 'is-global' : ''}`}
                      >
                        {scopeName(item.run_id)}
                      </span>
                    </span>
                  </span>
                  <span className="memory-entry-state">
                    <Status
                      tone={isExpired(item) ? 'gray' : validScope(item.run_id) ? 'green' : 'amber'}
                    >
                      {isExpired(item)
                        ? stateName(item)
                        : validScope(item.run_id)
                          ? '生效中'
                          : '范围待修正'}
                    </Status>
                    <time
                      dateTime={item.updated_at}
                      title={new Date(item.updated_at).toLocaleString('zh-CN')}
                    >
                      {memoryDate(item.updated_at)}
                    </time>
                  </span>
                </>
              );
              return (
                <article
                  data-memory-id={item.id}
                  className={`memory-entry ${checked.includes(item.id) ? 'is-checked' : ''} ${isExpired(item) ? 'is-inactive' : ''}`}
                  key={item.id}
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
          <EmptyState title={memories.length ? '没有找到匹配的内容' : '还没有记忆'}>
            {memories.length ? (
              <Button onClick={clearFilters}>清除筛选</Button>
            ) : (
              <Button icon={<Plus size={15} />} onClick={() => openCreate()}>
                记录第一条记忆
              </Button>
            )}
          </EmptyState>
        ) : null}
      </section>
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
            <dd>供同一用户与 Agent 在不同工作区使用</dd>
          </div>
          <div>
            <dt>工作区</dt>
            <dd>仅供对应工作区使用</dd>
          </div>
          <div>
            <dt>暂停 / 过期</dt>
            <dd>暂停使用或超过有效期后，不再用于日常对话；记忆正文和变更记录仍会保留。</dd>
          </div>
          <div>
            <dt>恢复</dt>
            <dd>恢复后立即生效，并清除原有效期。如需限时使用，请在详情中重新设置。</dd>
          </div>
        </dl>
        <p className="field-hint">
          添加的记忆按原文保存，用户与 Agent
          来自当前服务配置。工作区路径由服务端解析；记忆按相关性检索，不保证每次对话都引用。
          搜索、排序和筛选仅覆盖当前加载的记录（最多 1000 条）。
        </p>
      </Modal>
    </div>
  );
}
