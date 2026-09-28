import { useEffect, useRef, useState } from 'react';
import { Plus, SlidersHorizontal, Pencil, Pause, Play, Trash2, Globe2, History, X, Check, ArrowLeft, Layers, Info } from 'lucide-react';
import { Button, Checkbox, Select, Modal, Input, TextArea, Toast, PageHeading, Status, Tabs, SearchField, DetailHeading, Field, EmptyState, SortButton, navigate } from '@icarus/ui';
import { initialMemories, isExpired, memoryDate, type MemoryEntry } from './demo';
import './memory.css';

type Draft = { memory: string; user_id: string; run_id: string; expiration_date: string; category: string };
type Editor = { id: string; memory: string; category: string; expiration_date: string };
const emptyDraft: Draft = { memory: '', user_id: 'lin', run_id: 'workspace:icarus', expiration_date: '', category: '手动记录' };
const scopeName = (scope: string) => scope === 'global' ? '全局' : 'Icarus 工作区';
const stateName = (memory: MemoryEntry) => memory.expiration_date === '1970-01-01' ? '已停用' : isExpired(memory) ? '已过期' : '生效中';

export default function MemoryApp({ active, hash }: { active: boolean; hash: string }) {
  const [memories, setMemories] = useState<MemoryEntry[]>(initialMemories);
  const section = hash.split('?')[0].split('/')[2];
  const tab = ['all', 'active', 'expired'].includes(section) ? section : 'all';
  const selectedId = new URLSearchParams(hash.split('?')[1]).get('entry');
  const selected = memories.find(item => item.id === selectedId);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [scope, setScope] = useState('all');
  const [user, setUser] = useState('all');
  const [showFilters, setShowFilters] = useState(false);
  const [descending, setDescending] = useState(true);
  const [selecting, setSelecting] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [editError, setEditError] = useState('');
  const [create, setCreate] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [error, setError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<MemoryEntry | null>(null);
  const [about, setAbout] = useState(false);
  const detailRef = useRef<HTMLElement>(null);

  function go(nextTab = tab, id?: string, replace = false) {
    navigate(`#/memory/${nextTab}${id ? `?entry=${encodeURIComponent(id)}` : ''}`, replace);
  }

  useEffect(() => {
    if (active && selectedId && !selected) go(tab, undefined, true);
  }, [active, selectedId, selected, tab]);

  useEffect(() => {
    if (!active) { setCreate(false); setDeleteTarget(null); setAbout(false); }
  }, [active]);

  useEffect(() => { setChecked([]); }, [search, category, scope, user, tab]);
  useEffect(() => {
    if (active && selectedId && window.matchMedia('(max-width: 700px)').matches) detailRef.current?.focus();
  }, [active, selectedId]);

  const activeCount = memories.filter(item => !isExpired(item)).length;
  const categories = [...new Set([...initialMemories.map(item => item.metadata.category), ...memories.map(item => item.metadata.category)])]
    .filter(name => memories.some(item => item.metadata.category === name));
  const users = [...new Set(memories.map(item => item.user_id))];
  const filtered = memories.filter(item => (tab === 'all' || (tab === 'active' ? !isExpired(item) : isExpired(item)))
    && (category === 'all' || item.metadata.category === category) && (scope === 'all' || item.run_id === scope) && (user === 'all' || item.user_id === user)
    && `${item.memory} ${item.metadata.category} ${item.user_id}`.toLowerCase().includes(search.trim().toLowerCase()))
    .sort((a, b) => (descending ? -1 : 1) * (Date.parse(a.updated_at) - Date.parse(b.updated_at)));
  const groups = categories.map(name => ({ name, items: filtered.filter(item => item.metadata.category === name) })).filter(group => group.items.length > 0);
  const checkedItems = filtered.filter(item => checked.includes(item.id));
  const filterCount = [category, scope, user].filter(value => value !== 'all').length;

  function clearFilters() {
    setSearch(''); setCategory('all'); setScope('all'); setUser('all'); setChecked([]); go('all');
  }

  function startEdit(item: MemoryEntry) {
    setEditor({ id: item.id, memory: item.memory, category: item.metadata.category, expiration_date: item.expiration_date ?? '' });
    setEditError('');
    if (window.matchMedia('(max-width: 700px)').matches) go(tab);
    requestAnimationFrame(() => document.getElementById(`memory-editor-${item.id}`)?.focus());
  }

  function saveEdit() {
    if (!editor) return;
    if (!editor.memory.trim()) { setEditError('请先填写记忆内容。'); return; }
    const now = new Date().toISOString();
    setMemories(items => items.map(item => item.id === editor.id ? {
      ...item, memory: editor.memory.trim(), metadata: { category: editor.category.trim() || '手动记录' },
      expiration_date: editor.expiration_date || null, updated_at: now,
      history: [{ date: memoryDate(now), label: '修正了记忆', content: item.memory }, ...item.history],
    } : item));
    setEditor(null); Toast.success('已保存修改');
  }

  function openCreate() {
    setDraft({ ...emptyDraft, user_id: user === 'all' ? 'lin' : user, run_id: scope === 'all' ? emptyDraft.run_id : scope, category: category === 'all' ? '手动记录' : category });
    setError(''); setCreate(true);
  }

  function createMemory() {
    if (!draft.memory.trim()) { setError('请先填写记忆内容。'); return; }
    if (!draft.user_id.trim()) { setError('请填写所属用户。'); return; }
    const now = new Date().toISOString();
    const item: MemoryEntry = {
      id: `mem_${crypto.randomUUID().slice(0, 8)}`, memory: draft.memory.trim(), user_id: draft.user_id.trim(), agent_id: 'icarus', run_id: draft.run_id,
      expiration_date: draft.expiration_date || null, metadata: { category: draft.category.trim() || '手动记录' }, created_at: now, updated_at: now,
      history: [{ date: memoryDate(now), label: '创建了记忆', content: draft.memory.trim() }],
    };
    setMemories(items => [item, ...items]);
    setSearch(''); setCategory('all'); setScope('all'); setUser('all'); setChecked([]); setCreate(false);
    go('all', item.id); Toast.success('已添加记忆');
  }

  function changeStatus(items: MemoryEntry[], restore: boolean) {
    const ids = items.filter(item => restore ? isExpired(item) : !isExpired(item)).map(item => item.id);
    if (!ids.length) return;
    const now = new Date().toISOString();
    setMemories(previous => previous.map(item => ids.includes(item.id) ? {
      ...item, expiration_date: restore ? null : '1970-01-01', updated_at: now,
      history: [{ date: memoryDate(now), label: restore ? '恢复为长期有效' : '暂停使用', content: undefined }, ...item.history],
    } : item));
    setChecked([]);
    if (selectedId && ids.includes(selectedId) && tab !== 'all') go(tab, undefined, true);
    Toast.success(restore ? `已将 ${ids.length} 条记忆恢复为长期有效` : `已暂停 ${ids.length} 条记忆`);
  }

  return <div className="page memory-page">
    <PageHeading title="记忆" context={<span className="heading-count">{memories.length} 条</span>}>
      <Button className="icon-button" type="tertiary" theme="borderless" icon={<Info size={17} />} aria-label="记忆规则" title="记忆规则" onClick={() => setAbout(true)} />
      <Button theme="solid" icon={<Plus size={16} />} onClick={openCreate}>添加记忆</Button>
    </PageHeading>
    <Tabs value={tab} onChange={id => { setEditor(null); go(id); }} items={[{ id: 'all', label: '全部', count: memories.length }, { id: 'active', label: '生效中', count: activeCount }, { id: 'expired', label: '已失效', count: memories.length - activeCount }]} />
    <div className="toolbar">
      <SearchField value={search} onChange={setSearch} placeholder="搜索记忆…" />
      <Button type="tertiary" theme={showFilters || filterCount ? 'light' : 'borderless'} icon={<SlidersHorizontal size={16} />} onClick={() => setShowFilters(value => !value)} aria-expanded={showFilters}>筛选{filterCount ? ` ${filterCount}` : ''}</Button>
      <span className="toolbar-spacer" />
      <SortButton descending={descending} onClick={() => setDescending(value => !value)} />
      <Button type="tertiary" theme={selecting ? 'light' : 'borderless'} onClick={() => { setSelecting(value => !value); setChecked([]); }}>{selecting ? '退出多选' : '多选'}</Button>
    </div>
    {showFilters && <div className="filter-panel">
      <Field label="分类"><Select aria-label="筛选记忆分类" value={category} onChange={value => setCategory(String(value))} optionList={[{ value: 'all', label: '全部分类' }, ...categories.map(name => ({ value: name, label: name }))]} /></Field>
      <Field label="作用范围"><Select aria-label="筛选作用范围" value={scope} onChange={value => setScope(String(value))} optionList={[{ value: 'all', label: '所有范围' }, { value: 'workspace:icarus', label: 'Icarus 工作区' }, { value: 'global', label: '全局' }]} /></Field>
      <Field label="所属用户"><Select aria-label="筛选用户" value={user} onChange={value => setUser(String(value))} optionList={[{ value: 'all', label: '所有用户' }, ...users.map(name => ({ value: name, label: name }))]} /></Field>
    </div>}
    {(filterCount > 0 || search) && <div className="filter-summary"><span>{filtered.length} 条结果</span>{category !== 'all' && <span className="filter-chip">{category}</span>}{scope !== 'all' && <span className="filter-chip">{scopeName(scope)}</span>}{user !== 'all' && <span className="filter-chip">{user}</span>}<button className="link-button" onClick={clearFilters}>清除筛选<X size={12} /></button></div>}
    {selecting && <div className="selection-toolbar" aria-label="批量操作">
      <Checkbox aria-label="选择当前结果" checked={filtered.length > 0 && checkedItems.length === filtered.length} indeterminate={checkedItems.length > 0 && checkedItems.length < filtered.length} onChange={() => setChecked(checkedItems.length === filtered.length ? [] : filtered.map(item => item.id))} />
      <span>已选 <strong>{checkedItems.length}</strong> 条</span><span className="toolbar-spacer" />
      <Button type="tertiary" theme="borderless" icon={<Pause size={14} />} disabled={!checkedItems.some(item => !isExpired(item))} onClick={() => changeStatus(checkedItems, false)}>暂停所选</Button>
      <Button type="tertiary" theme="borderless" icon={<Play size={14} />} disabled={!checkedItems.some(isExpired)} onClick={() => changeStatus(checkedItems, true)}>恢复所选</Button>
    </div>}
    <div className={`memory-workspace ${selected ? 'has-inspector' : ''}`}>
      <section className="memory-board" aria-label="记忆列表">
        {groups.length > 0 ? <div className="memory-groups">{groups.map(group => <section className="memory-group" key={group.name} aria-label={group.name}>
          <header className="memory-group-heading"><span className="group-marker" /><h2>{group.name}</h2><span>{group.items.length}</span></header>
          <div className="memory-group-items">{group.items.map(item => <article id={`memory-row-${item.id}`} data-memory-id={item.id} className={`memory-entry ${selectedId === item.id ? 'is-current' : ''} ${checked.includes(item.id) ? 'is-checked' : ''} ${isExpired(item) ? 'is-inactive' : ''}`} key={item.id}>
            {selecting && <Checkbox className="memory-checkbox" aria-label={`选择记忆：${item.memory}`} checked={checked.includes(item.id)} onChange={() => setChecked(ids => ids.includes(item.id) ? ids.filter(id => id !== item.id) : [...ids, item.id])} />}
            <div className="memory-entry-main">
              {editor?.id === item.id ? <div className="inline-editor" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setEditor(null); } if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); saveEdit(); } }}>
                <TextArea id={`memory-editor-${item.id}`} aria-label="修正记忆内容" value={editor.memory} onChange={memory => setEditor({ ...editor, memory })} autosize={{ minRows: 3, maxRows: 10 }} />
                <details className="inline-extras"><summary>分类与有效期</summary><div className="form-row"><Field label="分类"><Input aria-label="修改记忆分类" value={editor.category} onChange={value => setEditor({ ...editor, category: value })} /></Field><Field label="有效期"><Input aria-label="修改记忆有效期" type="date" value={editor.expiration_date} onChange={value => setEditor({ ...editor, expiration_date: value })} /></Field></div></details>
                {editError && <p role="alert" className="form-error">{editError}</p>}
                <div className="inline-editor-actions"><span>Ctrl ↵ 保存</span><Button type="tertiary" theme="borderless" onClick={() => setEditor(null)}>取消修改</Button><Button theme="solid" icon={<Check size={14} />} onClick={saveEdit}>保存修改</Button></div>
              </div> : <>
                <button className="memory-content" aria-label={`查看记忆：${item.memory}`} onClick={() => go(tab, item.id)}>{item.memory}</button>
                <div className="memory-meta"><span>{item.run_id === 'global' ? <Globe2 size={12} /> : <Layers size={12} />}{scopeName(item.run_id)}</span><span>{item.user_id}</span><time dateTime={item.updated_at} title={new Date(item.updated_at).toLocaleString('zh-CN')}>{memoryDate(item.updated_at)}</time></div>
                <div className="memory-entry-footer"><span>{isExpired(item) && <Status tone="gray">{stateName(item)}</Status>}</span><div className="entry-actions">
                  <Button className="icon-button" type="tertiary" theme="borderless" icon={<Pencil size={15} />} aria-label={`修正记忆：${item.memory}`} title="修正内容" onClick={() => startEdit(item)} />
                  <Button className="icon-button" type="tertiary" theme="borderless" icon={isExpired(item) ? <Play size={15} /> : <Pause size={15} />} aria-label={`${isExpired(item) ? '恢复' : '暂停'}记忆：${item.memory}`} title={isExpired(item) ? '恢复为长期有效' : '暂停使用'} onClick={() => changeStatus([item], isExpired(item))} />
                  <Button className="icon-button" type="tertiary" theme="borderless" icon={<History size={15} />} aria-label={`变更记录：${item.memory}`} title="变更记录" onClick={() => go(tab, item.id)} />
                </div></div>
              </>}
            </div>
          </article>)}</div>
        </section>)}</div> : <EmptyState title={memories.length ? '没有找到匹配的内容' : '还没有记忆'}>{memories.length ? <Button onClick={clearFilters}>清除筛选</Button> : <Button icon={<Plus size={15} />} onClick={openCreate}>添加记忆</Button>}</EmptyState>}
      </section>
      {selected && <aside ref={detailRef} tabIndex={-1} className="memory-inspector" aria-label="记忆详情">
        <DetailHeading label="记忆详情" onClose={() => go(tab)} />
        <div className="inspector-content"><button className="link-button inspector-back" onClick={() => go(tab)}><ArrowLeft size={14} />返回记忆列表</button>
          <span className="inspector-category">{selected.metadata.category}</span><p className="inspector-memory">{selected.memory}</p>
          <Status tone={isExpired(selected) ? 'gray' : 'green'}>{stateName(selected)}</Status>
          <div className="inspector-actions"><Button icon={<Pencil size={14} />} onClick={() => startEdit(selected)}>修正内容</Button><Button type="tertiary" theme="borderless" icon={isExpired(selected) ? <Play size={14} /> : <Pause size={14} />} onClick={() => changeStatus([selected], isExpired(selected))}>{isExpired(selected) ? '恢复使用' : '暂停使用'}</Button></div>
          <div className="detail-section"><dl className="detail-properties"><div><dt>所属用户</dt><dd>{selected.user_id}</dd></div><div><dt>作用范围</dt><dd>{scopeName(selected.run_id)}</dd></div><div><dt>有效期</dt><dd>{selected.expiration_date === '1970-01-01' ? '已停用' : selected.expiration_date || '长期有效'}</dd></div></dl></div>
          <div className="detail-section"><h3>变更记录</h3><div className="memory-timeline">{selected.history.map((entry, index) => <div className="timeline-item" key={`${entry.date}-${index}`}><i /><div><p>{entry.label}</p><time>{entry.date}</time>{entry.content && <blockquote>{entry.content}</blockquote>}</div></div>)}</div></div>
          <div className="detail-section"><Button type="danger" theme="borderless" icon={<Trash2 size={14} />} onClick={() => setDeleteTarget(selected)}>删除这条记忆</Button></div>
        </div>
      </aside>}
    </div>
    <Modal title="添加记忆" visible={create} onCancel={() => setCreate(false)} onOk={createMemory} okText="保存记忆" width={520}>
      <Field label="记忆内容"><TextArea aria-label="记忆内容" value={draft.memory} onChange={memory => setDraft({ ...draft, memory })} autosize={{ minRows: 4, maxRows: 8 }} placeholder="写下一条偏好、事实或约定…" /></Field>
      <div className="form-row"><Field label="作用范围"><Select aria-label="记忆作用范围" value={draft.run_id} onChange={value => setDraft({ ...draft, run_id: String(value) })} optionList={[{ value: 'workspace:icarus', label: 'Icarus 工作区' }, { value: 'global', label: '全局' }]} /></Field><Field label="所属用户"><Input aria-label="所属用户" value={draft.user_id} onChange={user_id => setDraft({ ...draft, user_id })} /></Field></div>
      <details className="form-extras"><summary>分类与有效期</summary><div className="form-row"><Field label="分类"><Input aria-label="记忆分类" value={draft.category} onChange={category => setDraft({ ...draft, category })} /></Field><Field label="有效期" hint="留空为长期有效"><Input aria-label="记忆有效期" type="date" value={draft.expiration_date} onChange={expiration_date => setDraft({ ...draft, expiration_date })} /></Field></div></details>
      {error && <p role="alert" className="form-error">{error}</p>}
    </Modal>
    <Modal title="删除这条记忆？" visible={deleteTarget !== null} onCancel={() => setDeleteTarget(null)} okText="确认删除" cancelText="保留记忆" okButtonProps={{ type: 'danger' }} onOk={() => {
      if (!deleteTarget) return;
      const id = deleteTarget.id;
      setMemories(items => items.filter(item => item.id !== id)); setChecked(ids => ids.filter(value => value !== id));
      if (editor?.id === id) setEditor(null);
      if (selectedId === id) go(tab, undefined, true);
      setDeleteTarget(null); Toast.success('已删除记忆');
    }}><p className="danger-copy">{deleteTarget?.memory}</p><p className="field-hint">删除后无法恢复。若只是暂时不用，可以选择暂停。</p></Modal>
    <Modal title="记忆规则" visible={about} onCancel={() => setAbout(false)} footer={<Button theme="solid" onClick={() => setAbout(false)}>知道了</Button>}>
      <dl className="detail-properties"><div><dt>全局</dt><dd>同一用户与 Agent 的跨工作区记忆</dd></div><div><dt>工作区</dt><dd>只在对应项目中使用</dd></div><div><dt>暂停 / 过期</dt><dd>不参与正常召回，内容与历史仍保留</dd></div><div><dt>恢复</dt><dd>重新设为长期有效</dd></div></dl><p className="field-hint">当前为内存演示，未连接 Mem0；搜索仅筛选当前列表，刷新后恢复示例数据。</p>
    </Modal>
  </div>;
}
