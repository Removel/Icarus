import { StrictMode, useRef, useState, type MouseEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Brain, ChevronDown, FileText, Globe2, Layers, Link2, Pencil, Search, X } from 'lucide-react';
import { BrandMark, Button, EmptyState, Modal, PageHeading, SearchField, Select, Status, TextArea } from '@icarus/ui';
import { initialMemories, isExpired, memoryDate, type MemoryEntry } from '../../../../packages/memory-app/src/demo';
import { initialBases, kindLabel, type Source, type WikiPage } from '../../../../packages/knowledge-app/src/demo';
import Reading, { excerpt } from '../../../../packages/knowledge-app/src/Reading';
import '@icarus/ui/styles.css';
import '../workspace.css';
import '../../../../packages/knowledge-app/src/knowledge.css';
import './study.css';

function BranchLines({ left, right }: { left: number; right: number }) {
  return <svg className="study-branch-lines" viewBox="0 0 1000 520" preserveAspectRatio="none" aria-hidden="true">
    {Array.from({ length: left }, (_, index) => { const y = (index + 1) * 520 / (left + 1); return <path key={`left-${index}`} d={`M 310 260 C 267 260, 273 ${y}, 230 ${y}`} />; })}
    {Array.from({ length: right }, (_, index) => { const y = (index + 1) * 520 / (right + 1); return <path key={`right-${index}`} d={`M 690 260 C 733 260, 727 ${y}, 770 ${y}`} />; })}
  </svg>;
}

// Deliberately isolated design study. Business pages and their state are untouched.
function MemoryStudy() {
  const [memories, setMemories] = useState(initialMemories);
  const [user, setUser] = useState('lin');
  const [scope, setScope] = useState('workspace:icarus');
  const [category, setCategory] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [discard, setDiscard] = useState(false);
  const [error, setError] = useState('');
  const trigger = useRef<HTMLElement | null>(null);
  const memoryFocus = useRef<HTMLHeadingElement>(null);
  const selected = memories.find(item => item.id === selectedId);
  const context = memories.filter(item => item.user_id === user && (scope === 'workspace:icarus' ? ['global', scope].includes(item.run_id) : item.run_id === 'global'));
  const matching = context.filter(item => `${item.memory} ${item.metadata.category}`.toLowerCase().includes(query.trim().toLowerCase()));
  const active = matching.filter(item => !isExpired(item));
  const archived = matching.filter(isExpired);
  const categories = [...new Set(active.map(item => item.metadata.category))];
  const categoryItems = active.filter(item => item.metadata.category === category);

  function chooseCategory(name: string | null) {
    setCategory(name);
    requestAnimationFrame(() => memoryFocus.current?.focus({ preventScroll: true }));
  }

  function open(item: MemoryEntry, event: MouseEvent<HTMLButtonElement>) {
    trigger.current = event.currentTarget;
    setSelectedId(item.id); setEditing(false); setDiscard(false); setError('');
  }
  function close() {
    if (editing && selected && draft !== selected.memory) { setDiscard(true); return; }
    setSelectedId(null); setEditing(false);
  }
  function save() {
    if (!draft.trim()) { setError('请先填写记忆内容。'); return; }
    const now = new Date().toISOString();
    setMemories(items => items.map(item => item.id === selectedId ? {
      ...item, memory: draft.trim(), updated_at: now,
      history: [{ date: memoryDate(now), label: '修正了记忆', content: item.memory }, ...item.history],
    } : item));
    setEditing(false); setDiscard(false);
  }
  return <div className="page study-page">
    <PageHeading title="记忆" context={<span className="heading-count">探索上下文</span>}>
      <Button className="icon-button" type="tertiary" theme={searching ? 'light' : 'borderless'} icon={<Search size={18} />} aria-label="查找记忆" onClick={() => { setSearching(!searching); setQuery(''); }} />
      <a className="study-manage" href="/#/memory/all">管理记忆<ArrowUpRight size={14} /></a>
    </PageHeading>
    <div className="study-context-controls"><span>关于</span><Select aria-label="记忆所属用户" value={user} onChange={value => { setUser(String(value)); setCategory(null); }} optionList={[{ value: 'lin', label: 'lin' }, { value: 'chen', label: 'chen' }]} /><span>在</span><Select aria-label="记忆使用情境" value={scope} onChange={value => { setScope(String(value)); setCategory(null); }} optionList={[{ value: 'workspace:icarus', label: 'Icarus 工作区' }, { value: 'global', label: '所有工作区' }]} /><span className="toolbar-spacer" /><span className="study-context-count">{context.filter(item => !isExpired(item)).length} 条生效记忆</span></div>
    {searching && <div className="study-search"><SearchField value={query} onChange={setQuery} placeholder="在当前上下文中查找…" />{query && <span>{matching.length} 条匹配</span>}</div>}
    {query.trim() ? <section className="study-search-results" aria-label="记忆搜索结果">{matching.length ? matching.map(item => <button key={item.id} onClick={event => open(item, event)}><Brain size={16} /><span><strong>{item.memory}</strong><small>{item.metadata.category} · {item.run_id === 'global' ? '全局' : 'Icarus 工作区'}{isExpired(item) ? ' · 已失效' : ''}</small></span><ArrowUpRight size={15} /></button>) : <EmptyState title="没有匹配的记忆" />}</section> : <>
      <nav className="study-explore-trail" aria-label="记忆探索路径"><button onClick={() => chooseCategory(null)} aria-current={!category ? 'page' : undefined}>{user} 的上下文</button>{category && <><ArrowRight size={12} /><span>{category}</span></>}</nav>
      <section className="study-relation-stage" aria-label={`${user} 的记忆关系空间`}>
        <BranchLines left={1} right={category ? categoryItems.length : categories.length} />
        <article className="study-focus-card"><div className="study-focus-eyebrow"><Brain size={15} />{category ? '记忆分类' : '当前关注'}</div><h2 ref={memoryFocus} tabIndex={-1}>{category ?? `${user} 的上下文`}</h2><p>{category ? `沿分类查看 ${user} 的原始记忆，每条内容仍可独立核对和修正。` : scope === 'global' ? '从全局偏好出发，看看哪些信息会跨工作区保留。' : '从当前工作区出发，看看 Icarus 记住了哪些偏好与约定。'}</p><div className="study-focus-foot"><span>{category ? categoryItems.length : active.length} 条生效记忆</span><span>{category ? '原文记录' : scope === 'global' ? '跨工作区' : '含全局偏好'}</span></div></article>
        <div className="study-node-lane study-node-left"><span className="study-lane-label">{category ? '来自上下文' : '所属用户'}</span><button className="study-node" style={{ top: '50%' }} onClick={() => chooseCategory(null)} aria-label="回到用户上下文"><span className="study-node-type">{category ? <Layers size={14} /> : <Globe2 size={14} />}{category ? '返回上一步' : '上下文边界'}</span><strong>{category ? `${user} 的上下文` : user}</strong><p>{scope === 'global' ? '所有工作区' : 'Icarus 工作区 + 全局'}</p></button></div>
        <div className="study-node-lane study-node-right"><span className="study-lane-label">{category ? '包含的记忆' : '沿分类探索'}</span>{category ? categoryItems.map((item, index) => <button key={item.id} className="study-node study-memory-node" data-memory-id={item.id} style={{ top: `${(index + 1) * 100 / (categoryItems.length + 1)}%` }} onClick={event => open(item, event)} aria-label={`查看记忆：${item.memory}`}><span className="study-node-type">{item.run_id === 'global' ? '全局' : 'Icarus 工作区'}<ArrowUpRight size={13} /></span><p>{item.memory}</p></button>) : categories.map((name, index) => <button key={name} className="study-node" style={{ top: `${(index + 1) * 100 / (categories.length + 1)}%` }} onClick={() => chooseCategory(name)}><span className="study-node-type">{active.filter(item => item.metadata.category === name).length} 条记忆<ArrowRight size={13} /></span><strong>{name}</strong><p>{active.find(item => item.metadata.category === name)?.memory}</p></button>)}</div>
      </section><div className="study-space-caption"><span>连线表示用户、上下文、分类的归属关系</span><span>点选一个方向，继续探索</span></div>
    </>}
    {archived.length > 0 && <details className="study-archive"><summary><ChevronDown size={14} /><span>{archived.length} 条已停用或过期的记忆</span><span className="toolbar-spacer" /><small>仍保留内容和记录</small></summary><div>{archived.map(item => <div className="study-archive-fragment" key={item.id}><Status tone="gray">{item.expiration_date === '1970-01-01' ? '已停用' : '已过期'}</Status><button className="study-memory-fragment" onClick={event => open(item, event)}>{item.memory}</button></div>)}</div></details>}
    <Modal className="study-detail-modal" title={editing ? '修正这条记忆' : '记忆详情'} visible={Boolean(selected)} onCancel={close} afterClose={() => trigger.current?.focus({ preventScroll: true })} width={560} maskClosable={!editing} footer={selected && (discard ? <div className="study-modal-actions"><span>修改尚未保存</span><Button onClick={() => { setSelectedId(null); setDiscard(false); setEditing(false); }}>放弃修改</Button><Button theme="solid" onClick={() => setDiscard(false)}>继续编辑</Button></div> : editing ? <div className="study-modal-actions"><span>只修改选中的原句</span><Button onClick={() => { if (draft !== selected.memory) setDiscard(true); else setEditing(false); }}>取消</Button><Button theme="solid" onClick={save}>保存修改</Button></div> : <div className="study-modal-actions"><span>{memoryDate(selected.updated_at)} 更新</span><Button icon={<Pencil size={14} />} theme="solid" onClick={() => { setDraft(selected.memory); setEditing(true); setError(''); }}>修正内容</Button></div>)}>
      {selected && <div className="study-memory-detail"><div className="study-detail-eyebrow">{selected.metadata.category}</div>{editing ? <><TextArea aria-label="修正记忆原文" value={draft} onChange={setDraft} autosize={{ minRows: 4, maxRows: 10 }} autoFocus />{error && <p className="form-error" role="alert">{error}</p>}</> : <p className="study-detail-quote">{selected.memory}</p>}
        <div className="study-detail-context"><span>{selected.user_id}</span><span>·</span><span>{selected.run_id === 'global' ? '所有工作区' : 'Icarus 工作区'}</span><span>·</span><Status tone={isExpired(selected) ? 'gray' : 'green'}>{selected.expiration_date === '1970-01-01' ? '已停用' : isExpired(selected) ? '已过期' : selected.expiration_date ? `有效至 ${selected.expiration_date}` : '长期有效'}</Status></div>
        {!editing && <details className="study-history"><summary>变更记录<span>{selected.history.length}</span><ChevronDown size={14} /></summary>{selected.history.map((entry, index) => <div key={index}><span>{entry.label}</span><time>{entry.date}</time>{entry.content && <p>{entry.content}</p>}</div>)}</details>}
      </div>}
    </Modal>
  </div>;
}

type ReadingTarget = { page?: WikiPage; source?: Source };
type ExploreTarget = { kind: 'page' | 'source'; id: string };
function KnowledgeStudy() {
  const [baseName, setBaseName] = useState(initialBases[0].name);
  const base = initialBases.find(item => item.name === baseName)!;
  const [trail, setTrail] = useState<ExploreTarget[]>([{ kind: 'page', id: initialBases[0].pages[0].path }]);
  const focus = trail.at(-1);
  const focusPage = focus?.kind === 'page' ? base.pages.find(item => item.path === focus.id) : undefined;
  const focusSource = focus?.kind === 'source' ? base.documents.find(item => item.hash === focus.id) : undefined;
  const sourceNodes = focusPage ? base.documents.filter(item => focusPage.sources.includes(item.name)) : [];
  const relatedPages = base.pages.filter(item => focusSource ? item.sources.includes(focusSource.name) : focusPage && item.path !== focusPage.path && item.sources.some(name => focusPage.sources.includes(name)));
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');
  const [reading, setReading] = useState<ReadingTarget | null>(null);
  const [showSources, setShowSources] = useState(false);
  const reader = useRef<HTMLDivElement>(null);
  const readScroll = useRef(0);
  const trigger = useRef<HTMLElement | null>(null);
  const focusHeading = useRef<HTMLHeadingElement>(null);
  const matches = base.pages.filter(item => `${item.title} ${item.content}`.toLowerCase().includes(query.trim().toLowerCase()));

  function step(target: ExploreTarget) {
    if (target.kind === focus?.kind && target.id === focus.id) return;
    setTrail(items => [...items, target]);
    requestAnimationFrame(() => focusHeading.current?.focus({ preventScroll: true }));
  }
  function readFocus(event: MouseEvent<HTMLButtonElement>) {
    if (focusPage) open(focusPage, event);
    else if (focusSource) { trigger.current = event.currentTarget; setReading({ source: focusSource }); }
  }

  function open(item: WikiPage, event: MouseEvent<HTMLButtonElement>) {
    trigger.current = event.currentTarget; setReading({ page: item }); setShowSources(false); readScroll.current = 0;
  }
  function openSource(document: Source) {
    if (!reading?.page) return;
    readScroll.current = reader.current?.scrollTop ?? 0; setReading({ page: reading.page, source: document });
    requestAnimationFrame(() => { if (reader.current) reader.current.scrollTop = 0; });
  }
  function backToPage() {
    if (!reading?.page) return;
    setReading({ page: reading.page }); requestAnimationFrame(() => { if (reader.current) reader.current.scrollTop = readScroll.current; });
  }

  return <div className="page study-page">
    <PageHeading title="知识库" context={<div className="knowledge-context"><span className="context-divider" /><Select className="base-switch" aria-label="选择知识库" value={baseName} onChange={value => { const next = initialBases.find(item => item.name === value)!; setBaseName(next.name); setQuery(''); setTrail(next.pages.length ? [{ kind: 'page', id: next.pages[0].path }] : []); }} optionList={initialBases.map(item => ({ value: item.name, label: item.name }))} /></div>}>
      <Button className="icon-button" type="tertiary" theme={searching ? 'light' : 'borderless'} icon={<Search size={18} />} aria-label="查找知识" onClick={() => { setSearching(!searching); setQuery(''); }} />
      <a className="study-manage" href="/#/knowledge/sources">管理资料<ArrowUpRight size={14} /></a>
    </PageHeading>
    <div className="study-collection-caption"><span>从一个关注点，沿来源与知识继续探索</span><span>{base.pages.length} 个知识页面 · {base.documents.length} 份资料</span></div>
    {searching && <div className="study-search"><SearchField value={query} onChange={setQuery} placeholder="直接查找知识内容…" /><span>搜索整个知识库</span></div>}
    {query.trim() ? <section className="study-search-results" aria-label="知识搜索结果">{matches.length ? matches.map(item => <button key={item.path} onClick={() => { setQuery(''); step({ kind: 'page', id: item.path }); }}><BookOpen size={16} /><span><strong>{item.title}</strong><small>{excerpt(item.content)}</small></span><ArrowRight size={15} /></button>) : <EmptyState title="没有找到匹配的知识" />}</section> : focusPage || focusSource ? <>
      <nav className="study-explore-trail" aria-label="知识探索路径"><Button className="icon-button" type="tertiary" theme="borderless" icon={<ArrowLeft size={16} />} aria-label="返回上一个关注点" disabled={trail.length < 2} onClick={() => setTrail(items => items.slice(0, -1))} />{trail.map((item, index) => <span key={`${item.id}-${index}`}><button aria-current={index === trail.length - 1 ? 'page' : undefined} onClick={() => setTrail(items => items.slice(0, index + 1))}>{item.kind === 'page' ? base.pages.find(p => p.path === item.id)?.title : base.documents.find(s => s.hash === item.id)?.name.replace(/\.[^.]+$/, '')}</button>{index < trail.length - 1 && <ArrowRight size={12} />}</span>)}</nav>
      <section className="study-relation-stage" aria-label="知识关系空间"><BranchLines left={sourceNodes.length} right={relatedPages.length} />
        <article className="study-focus-card"><div className="study-focus-eyebrow">{focusPage ? <BookOpen size={15} /> : <FileText size={15} />}{focusPage ? kindLabel[focusPage.kind] : '原始资料'}<span>当前关注</span></div><h2 ref={focusHeading} tabIndex={-1}>{focusPage?.title ?? focusSource?.name.replace(/\.[^.]+$/, '')}</h2><p>{excerpt(focusPage?.content ?? focusSource?.content ?? '')}</p><Button theme="solid" icon={<ArrowUpRight size={15} />} onClick={readFocus}>{focusPage ? '阅读全文' : '查看资料'}</Button><div className="study-focus-foot"><span>{focusPage ? `${sourceNodes.length} 份来源` : focusSource?.display_type}</span><span>{relatedPages.length} 篇{focusPage ? '同源知识' : '引用知识'}</span></div></article>
        <div className="study-node-lane study-node-left"><span className="study-lane-label">{focusPage ? '它来自哪里' : '来源节点'}</span>{sourceNodes.map((item, index) => <button className="study-node" key={item.hash} style={{ top: `${(index + 1) * 100 / (sourceNodes.length + 1)}%` }} onClick={() => step({ kind: 'source', id: item.hash })}><span className="study-node-type"><FileText size={13} />来源资料</span><strong>{item.name}</strong><p>{item.display_type}{item.pages != null ? ` · ${item.pages} 页` : ''}</p></button>)}{!sourceNodes.length && <div className="study-space-note"><FileText size={20} strokeWidth={1.3} /><p>{focusSource ? '从这份资料，继续看看它支持了哪些知识。' : '这个页面尚未关联来源。'}</p></div>}</div>
        <div className="study-node-lane study-node-right"><span className="study-lane-label">{focusPage ? '同一来源，还可以看' : '由此资料支持'}</span>{relatedPages.map((item, index) => <button className="study-node" key={item.path} style={{ top: `${(index + 1) * 100 / (relatedPages.length + 1)}%` }} onClick={() => step({ kind: 'page', id: item.path })}><span className="study-node-type"><BookOpen size={13} />{focusPage ? '共同来源' : '引用此资料'}<ArrowRight size={12} /></span><strong>{item.title}</strong><p>{focusPage ? item.sources.filter(name => focusPage.sources.includes(name)).map(name => name.replace(/\.[^.]+$/, '')).join('、') : kindLabel[item.kind]}</p></button>)}{!relatedPages.length && <div className="study-space-note"><Link2 size={20} strokeWidth={1.3} /><p>暂未发现其他同源页面。可以从来源继续探索。</p></div>}</div>
      </section><div className="study-space-caption"><span>连线：来源引用 · 同源页面</span><span>只展开当前关注点的一层关系</span></div>
      <div className="study-space-entrypoints"><span>换个起点</span>{base.pages.filter(item => !relatedPages.some(p => p.path === item.path) && item.path !== focusPage?.path).map(item => <button key={item.path} onClick={() => setTrail([{ kind: 'page', id: item.path }])}>{item.title}<ArrowUpRight size={12} /></button>)}</div>
    </> : <EmptyState title="这里还没有可以探索的知识" description="添加资料后，从知识内容和真实来源关系进入。" />}
    <Modal className="study-reader-modal" title={reading?.source && reading.page ? <button className="study-modal-back" onClick={backToPage}><ArrowLeft size={15} />返回知识页面</button> : reading?.source ? '原始资料' : '阅读知识'} visible={Boolean(reading)} width={840} onCancel={() => setReading(null)} afterClose={() => trigger.current?.focus({ preventScroll: true })} footer={reading && <div className="study-modal-actions">{!reading.source && reading.page && <Button type="tertiary" theme="borderless" icon={<Link2 size={14} />} aria-expanded={showSources} onClick={() => { setShowSources(!showSources); if (!showSources) requestAnimationFrame(() => reader.current?.querySelector('.study-reading-sources')?.scrollIntoView({ block: 'nearest' })); }}>来源 · {reading.page.sources.length}</Button>}<span /><Button onClick={() => setReading(null)}>完成阅读</Button></div>}>
      {reading && <div className="study-reader-scroll" ref={reader}><div className="study-detail-eyebrow">{reading.source ? '原始资料' : reading.page ? kindLabel[reading.page.kind] : ''} · {base.name}</div><h2>{reading.source?.name ?? reading.page?.title}</h2><Reading content={reading.source?.content ?? reading.page?.content ?? ''} />{!reading.source && reading.page && showSources && <section className="study-reading-sources" aria-label="来源资料"><h3>来源资料</h3>{reading.page.sources.map(name => { const document = base.documents.find(item => item.name === name); return document ? <button key={name} onClick={() => openSource(document)}><FileText size={16} /><span>{name}</span><ArrowUpRight size={14} /></button> : <p key={name}>{name} · 来源不可用</p>; })}</section>}</div>}
    </Modal>
  </div>;
}

function ContentStudy() {
  const [app, setApp] = useState(() => window.location.hash === '#knowledge' ? 'knowledge' : 'memory');
  return <div className="app-layout study-layout"><main className="shell-main"><div hidden={app !== 'memory'}><MemoryStudy /></div><div hidden={app !== 'knowledge'}><KnowledgeStudy /></div></main><aside className="app-dock" aria-label="Icarus 导航"><a className="brand" href="/" aria-label="返回原版 WebUI"><BrandMark size={27} /></a><nav aria-label="应用导航"><button className={`nav-link ${app === 'memory' ? 'active' : ''}`} aria-pressed={app === 'memory'} onClick={() => setApp('memory')}><Brain size={18} strokeWidth={1.7} />记忆</button><button className={`nav-link ${app === 'knowledge' ? 'active' : ''}`} aria-pressed={app === 'knowledge'} onClick={() => setApp('knowledge')}><BookOpen size={18} strokeWidth={1.7} />知识库</button></nav><div className="dock-tools"><a className="dock-tool" href="/" title="返回原版" aria-label="返回原版"><X size={18} /></a></div></aside><span className="demo-indicator"><i />内容组织设计 · 示例数据</span></div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><ContentStudy /></StrictMode>);
