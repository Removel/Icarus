import { useEffect, useState } from 'react';
import { Plus, SlidersHorizontal, Quote, Pencil, Pause, Play, Trash2, Globe2, Fingerprint, ArrowUpRight, CornerDownRight } from 'lucide-react';
import { Button, Select, Modal, Input, TextArea, Toast, PageHeading, Status, Tabs, SearchField, DetailHeading, Field, EmptyState, TableFooter, SortButton } from '@icarus/ui';
import { initialMemories, isExpired, memoryDate, type MemoryEntry } from './demo';

type Draft = { memory: string; user_id: string; run_id: string; expiration_date: string; category: string };
const emptyDraft: Draft = { memory: '', user_id: 'lin', run_id: 'workspace:icarus', expiration_date: '', category: '手动记录' };

export default function MemoryApp() {
  const [memories, setMemories] = useState<MemoryEntry[]>(initialMemories);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState('all');
  useEffect(() => {
    function syncSection() { if (location.hash.startsWith('#/memory')) { const next = location.hash.split('/')[2]; setTab(['all', 'active', 'expired'].includes(next) ? next : 'all'); } }
    syncSection();
    window.addEventListener('hashchange', syncSection);
    return () => window.removeEventListener('hashchange', syncSection);
  }, []);
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState('all');
  const [user, setUser] = useState('all');
  const [showFilters, setShowFilters] = useState(false);
  const [descending, setDescending] = useState(true);
  const [editor, setEditor] = useState<'create' | 'edit' | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [about, setAbout] = useState(false);
  useEffect(() => {
    if (!selectedId) return;
    function closeOnEscape(event: KeyboardEvent) { if (event.key === 'Escape') setSelectedId(null); }
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [selectedId]);
  const selected = memories.find(m => m.id === selectedId);
  const active = memories.filter(m => !isExpired(m)).length;
  const filtered = memories.filter(m => (tab === 'all' || (tab === 'active' ? !isExpired(m) : isExpired(m)))
    && (scope === 'all' || m.run_id === scope) && (user === 'all' || m.user_id === user)
    && `${m.memory} ${m.metadata.category} ${m.id}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (descending ? 1 : -1) * b.updated_at.localeCompare(a.updated_at));
  const users = [...new Set(memories.map(m => m.user_id))];

  function openEditor(mode: 'create' | 'edit') {
    setDraft(mode === 'edit' && selected ? { memory: selected.memory, user_id: selected.user_id, run_id: selected.run_id, expiration_date: selected.expiration_date ?? '', category: selected.metadata.category } : { ...emptyDraft });
    setError(''); setEditor(mode);
  }

  function saveMemory() {
    if (!draft.memory.trim()) { setError('请先填写记忆内容。'); return; }
    if (!draft.user_id.trim()) { setError('请填写这条记忆所属的用户 ID。'); return; }
    const now = new Date().toISOString();
    const payload = { memory: draft.memory.trim(), user_id: draft.user_id.trim(), run_id: draft.run_id, expiration_date: draft.expiration_date || null, metadata: { category: draft.category.trim() || '手动记录' }, updated_at: now };
    if (editor === 'edit' && selected) {
      setMemories(items => items.map(m => m.id === selected.id ? { ...m, ...payload, history: [{ date: memoryDate(now), label: '手动修正记忆', content: m.memory }, ...m.history] } : m));
    } else {
      const item: MemoryEntry = { ...payload, id: `mem_${crypto.randomUUID().slice(0, 8)}`, agent_id: 'icarus', created_at: now, history: [{ date: memoryDate(now), label: '手动创建记忆', content: payload.memory }] };
      setMemories(items => [item, ...items]); setSelectedId(item.id); setTab('all'); setScope('all'); setUser('all'); setSearch('');
    }
    setEditor(null); Toast.success('记忆已保存到本次演示');
  }

  function toggleExpiration() {
    if (!selected) return;
    const wasExpired = isExpired(selected);
    const now = new Date().toISOString();
    setMemories(items => items.map(m => m.id === selected.id ? { ...m, expiration_date: wasExpired ? null : '1970-01-01', updated_at: now, history: [{ date: memoryDate(now), label: wasExpired ? '恢复为长期有效' : '停止使用这条记忆' }, ...m.history] } : m));
    Toast.success(wasExpired ? '已恢复为长期有效' : '已停用，内容与历史仍然保留');
  }

  return <div className="page memory-page">
    <PageHeading title="我的记忆" description="保存对你重要的偏好和约定，随时查看、修改或暂停。">
      <Button className="secondary-action" theme="borderless" type="tertiary" icon={<SlidersHorizontal size={15} />} onClick={() => setAbout(true)}>记忆规则</Button>
      <Button theme="solid" icon={<Plus size={16} />} onClick={() => openEditor('create')}>添加记忆</Button>
    </PageHeading>
    <div className="metric-strip"><div className="metric"><strong>{String(memories.length).padStart(2, '0')}</strong><span>全部记忆</span></div><div className="metric"><strong>{String(active).padStart(2, '0')}</strong><span>正在生效</span></div><div className="metric"><strong>{String(users.length).padStart(2, '0')}</strong><span>关联用户</span></div><div className="metric-note"><Fingerprint size={14} />属于你的上下文，由你掌握</div></div>
    <Tabs value={tab} onChange={id => { setTab(id); location.hash = `/memory/${id}`; }} items={[{ id: 'all', label: '全部记忆', count: memories.length }, { id: 'active', label: '生效中', count: active }, { id: 'expired', label: '已失效', count: memories.length - active }]} />
    <div className="toolbar"><SearchField value={search} onChange={setSearch} placeholder="搜索记忆…" /><Button theme="light" type="tertiary" icon={<SlidersHorizontal size={15} />} onClick={() => setShowFilters(!showFilters)} aria-expanded={showFilters}>筛选</Button>{showFilters && <><Select aria-label="筛选作用范围" value={scope} onChange={v => setScope(String(v))} optionList={[{ value: 'all', label: '所有作用范围' }, { value: 'workspace:icarus', label: '当前工作区' }, { value: 'global', label: '全局记忆' }]} /><Select aria-label="筛选用户" value={user} onChange={v => setUser(String(v))} optionList={[{ value: 'all', label: '所有用户' }, ...users.map(u => ({ value: u, label: u }))]} /></>}<span className="toolbar-spacer" /></div>
    <div className={`workspace-split ${selected ? '' : 'no-detail'}`}>
      <section className="list-panel" aria-label="记忆列表">
        <table className="data-table"><colgroup><col /><col style={{ width: 82 }} /><col className="optional-column" style={{ width: 84 }} /><col className="mobile-hidden" style={{ width: 94 }} /></colgroup><thead><tr><th>记忆内容</th><th>状态</th><th className="optional-column">作用范围</th><th className="mobile-hidden"><SortButton descending={descending} onClick={() => setDescending(!descending)} /></th></tr></thead><tbody>{filtered.map(m => <tr key={m.id} className={m.id === selectedId ? 'selected' : ''}><td><button className="table-item" onClick={() => setSelectedId(m.id)} aria-label={`查看记忆：${m.memory}`}>{m.memory}</button><div className="item-sub"><span>{m.metadata.category}</span><span>·</span><span className="mono">{m.user_id}</span></div></td><td><Status tone={isExpired(m) ? 'gray' : 'green'}>{m.expiration_date === '1970-01-01' ? '已停用' : isExpired(m) ? '已过期' : '生效中'}</Status></td><td className="optional-column"><span className={`scope-label ${m.run_id === 'global' ? 'global' : ''}`}>{m.run_id === 'global' ? '全局' : '工作区'}</span></td><td className="text-muted mobile-hidden" style={{ fontSize: 10 }}>{memoryDate(m.updated_at).split(' ')[0]}</td></tr>)}</tbody></table>
        {filtered.length === 0 && <EmptyState><Button onClick={() => { setSearch(''); setScope('all'); setUser('all'); setTab('all'); }}>清除筛选</Button></EmptyState>}
        <TableFooter count={filtered.length} />
        <div className="callout" style={{ marginTop: 10, display: 'flex', gap: 10 }}><CornerDownRight size={15} style={{ marginTop: 3 }} /><span>记忆会随你的反馈而更新。发现不准确的内容，可以直接修正，或暂停使用。</span></div>
      </section>
      {selected && <button className="detail-backdrop" aria-label="返回记忆列表" onClick={() => setSelectedId(null)} />}{selected && <aside className="detail-panel" aria-label="记忆详情"><DetailHeading label="记忆详情" onClose={() => setSelectedId(null)} /><div className="detail-note"><Quote className="note-icon" size={21} strokeWidth={1.4} /><p>{selected.memory}</p><small>{selected.metadata.category} · {selected.run_id === 'global' ? '全局记忆' : 'Icarus 工作区'}</small></div><div className="detail-body"><dl className="detail-properties"><div><dt>记忆 ID</dt><dd className="mono">{selected.id}</dd></div><div><dt>所属用户</dt><dd className="mono">{selected.user_id}</dd></div><div><dt>关联 Agent</dt><dd className="mono">{selected.agent_id}</dd></div><div><dt>作用范围</dt><dd><Globe2 size={11} style={{ verticalAlign: -2, marginRight: 4 }} />{selected.run_id === 'global' ? '所有工作区' : 'Icarus'}</dd></div><div><dt>有效期</dt><dd>{selected.expiration_date === '1970-01-01' ? '已停用' : selected.expiration_date || '长期有效'}</dd></div></dl><div className="detail-actions"><Button icon={<Pencil size={13} />} onClick={() => openEditor('edit')}>修正内容</Button><Button type="tertiary" icon={isExpired(selected) ? <Play size={13} /> : <Pause size={13} />} onClick={toggleExpiration}>{isExpired(selected) ? '恢复使用' : '暂停使用'}</Button></div><div className="detail-section"><h3>变更记录</h3>{selected.history.slice(0, 4).map((h, i) => <div className="timeline-item" key={`${h.date}-${i}`}><span className="timeline-dot" /><div><p>{h.label}</p><small>{h.date}</small>{h.content && <p className="history-content">{h.content}</p>}</div></div>)}</div><Button type="danger" theme="borderless" icon={<Trash2 size={12} />} onClick={() => setConfirmDelete(true)} style={{ fontSize: 11, padding: 0 }}>删除这条记忆</Button></div></aside>}
    </div>
    <footer className="bottom-caption"><span>记忆由 Mem0 管理 · 演示未连接服务</span><span>A LITTLE CONTEXT GOES A LONG WAY ↗</span></footer>
    <Modal title={editor === 'edit' ? '修正记忆' : '添加一条记忆'} visible={editor !== null} onCancel={() => setEditor(null)} onOk={saveMemory} okText="保存记忆" cancelText="取消" width={540}>
      <Field label="记忆内容" hint="写下一个明确的事实或偏好。手动录入按原文保存。"><TextArea aria-label="记忆内容" value={draft.memory} onChange={memory => setDraft({ ...draft, memory })} autosize={{ minRows: 4, maxRows: 8 }} placeholder="例如：偏好简洁的界面，重要操作需要明确反馈。" /></Field>
      <div className="form-row"><Field label="所属用户 ID"><Input aria-label="所属用户 ID" value={draft.user_id} disabled={editor === 'edit'} onChange={user_id => setDraft({ ...draft, user_id })} /></Field><Field label="作用范围"><Select aria-label="记忆作用范围" value={draft.run_id} disabled={editor === 'edit'} onChange={v => setDraft({ ...draft, run_id: String(v) })} optionList={[{ value: 'workspace:icarus', label: 'Icarus 工作区' }, { value: 'global', label: '全局 · 所有工作区' }]} /></Field></div>
      <div className="form-row"><Field label="分类（可选）"><Input aria-label="记忆分类" value={draft.category} onChange={category => setDraft({ ...draft, category })} /></Field><Field label="有效期（可选）" hint="留空为长期有效。"><Input aria-label="记忆有效期" type="date" value={draft.expiration_date} onChange={expiration_date => setDraft({ ...draft, expiration_date })} /></Field></div>
      {error && <p className="form-error" role="alert">{error}</p>}<p className="field-hint">当前为交互演示，刷新页面会恢复示例数据。</p>
    </Modal>
    <Modal title="删除这条记忆？" visible={confirmDelete} onCancel={() => setConfirmDelete(false)} okText="确认删除" cancelText="保留记忆" okButtonProps={{ type: 'danger' }} onOk={() => { setMemories(items => items.filter(m => m.id !== selectedId)); setSelectedId(null); setConfirmDelete(false); Toast.success('已从本次演示中删除'); }}><p className="danger-copy">删除后无法在这里恢复。若只是暂时不希望使用，建议选择「暂停使用」。</p><div className="detail-note"><p>{selected?.memory}</p></div></Modal>
    <Modal title="哪些记忆会生效？" visible={about} onCancel={() => setAbout(false)} footer={<Button theme="solid" onClick={() => setAbout(false)}>知道了</Button>}><div className="callout">Icarus 会在当前用户与 Agent 范围内，检索全局记忆和当前工作区记忆。已过期或已停用的记忆不参与正常召回。</div><dl className="detail-properties"><div><dt>全局记忆</dt><dd>同一用户与 Agent 的跨工作区偏好</dd></div><div><dt>工作区记忆</dt><dd>只与当前项目有关的事实</dd></div><div><dt>暂停使用</dt><dd>保留内容和历史，随时恢复</dd></div></dl><p className="field-hint">这里的搜索是当前列表内的文本筛选。语义检索与召回分数将在接入 Mem0 后提供。</p><p className="field-hint"><ArrowUpRight size={12} /> 这些规则来自仓库中现有的 Memory 插件。</p></Modal>
  </div>;
}
