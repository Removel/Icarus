import { useEffect, useRef, useState } from 'react';
import { Plus, Upload, FileText, BookOpen, ArrowUpRight, RefreshCw, Trash2, Check, CheckCheck, ArrowRight, FolderOpen, X, Network, Pencil, ShieldCheck, CircleAlert, Loader2 } from 'lucide-react';
import { Button, Select, Modal, Input, TextArea, Toast, PageHeading, Status, Tabs, SearchField, DetailHeading, Field, EmptyState, TableFooter } from '@icarus/ui';
import { initialBases, kindLabel, type KnowledgeBase, type Source, type WikiPage } from './demo';
import Graph from './Graph';
import './knowledge.css';

function Reading({ content }: { content: string }) {
  return <div className="reading">{content.split('\n').map((line, i) => line.startsWith('## ') ? <h3 key={i}>{line.slice(3)}</h3> : <p key={i}>{line}</p>)}</div>;
}

export default function KnowledgeApp() {
  const [bases, setBases] = useState<KnowledgeBase[]>(initialBases);
  const [baseName, setBaseName] = useState(initialBases[0].name);
  const [tab, setTab] = useState('sources');
  useEffect(() => {
    function syncSection() { if (location.hash.startsWith('#/knowledge')) { const next = location.hash.split('/')[2]; setTab(['sources', 'pages', 'graph', 'quality'].includes(next) ? next : 'sources'); } }
    syncSection();
    window.addEventListener('hashchange', syncSection);
    return () => window.removeEventListener('hashchange', syncSection);
  }, []);
  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [pagePath, setPagePath] = useState('');
  useEffect(() => {
    if (!sourceId && !pagePath) return;
    function closeOnEscape(event: KeyboardEvent) { if (event.key === 'Escape') { setSourceId(null); setPagePath(''); } }
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [sourceId, pagePath]);
  const [upload, setUpload] = useState(false);
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);
  const [uploadError, setUploadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [create, setCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newError, setNewError] = useState('');
  const [reader, setReader] = useState<{ title: string; content: string } | null>(null);
  const [editingPage, setEditingPage] = useState(false);
  const [pageDraft, setPageDraft] = useState('');
  const [pageError, setPageError] = useState('');
  const [deleteSource, setDeleteSource] = useState(false);
  const [linted, setLinted] = useState(false);
  const [lintBusy, setLintBusy] = useState(false);
  const [fixed, setFixed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const base = bases.find(b => b.name === baseName)!;
  const selected = base.documents.find(d => d.hash === sourceId);
  const selectedPage = base.pages.find(p => p.path === pagePath);
  const documents = base.documents.filter(d => (type === 'all' || d.display_type === type) && d.name.toLowerCase().includes(search.toLowerCase()));
  const pages = base.pages.filter(p => (type === 'all' || p.kind === type) && p.title.toLowerCase().includes(search.toLowerCase()));
  const ready = base.documents.filter(d => d.phase === 'ready').length;
  const related = selected ? base.pages.filter(p => p.sources.includes(selected.name)) : [];

  function updateBase(name: string, update: (b: KnowledgeBase) => KnowledgeBase) { setBases(items => items.map(b => b.name === name ? update(b) : b)); }
  function switchBase(name: string) { setBaseName(name); setSourceId(null); setPagePath(''); setSearch(''); setType('all'); setLinted(false); setFixed(false); }
  function switchTab(id: string) { setTab(id); setSearch(''); setType('all'); location.hash = `/knowledge/${id}`; }
  function schedule(fn: () => void, ms = 1200) { timers.current.push(setTimeout(fn, ms)); }
  function recompile(source: Source) {
    const name = baseName;
    updateBase(name, b => ({ ...b, documents: b.documents.map(d => d.hash === source.hash ? { ...d, phase: 'compiling' } : d) }));
    schedule(() => { updateBase(name, b => ({ ...b, documents: b.documents.map(d => d.hash === source.hash ? { ...d, phase: 'ready' } : d) })); Toast.success('演示编译完成'); });
  }
  function chooseFiles(incoming: FileList | File[]) {
    const list = Array.from(incoming);
    if (list.some(f => !/\.(pdf|md|txt|docx)$/i.test(f.name))) { setUploadError('此 Demo 支持选择 PDF、Markdown、TXT 和 DOCX 文件。'); return; }
    if (list.some(f => f.size > 100 * 1024 * 1024) || [...files, ...list].reduce((n, f) => n + f.size, 0) > 500 * 1024 * 1024) { setUploadError('单个文件上限 100 MB，单次导入合计上限 500 MB。'); return; }
    setFiles(previous => [...previous, ...list.map(f => ({ name: f.name, size: f.size }))].filter((f, i, all) => all.findIndex(x => x.name === f.name) === i)); setUploadError('');
  }
  function startImport() {
    if (!files.length) { setUploadError('先选择文件，或使用示例文件体验流程。'); return; }
    const name = baseName;
    const incoming = files.filter(f => !base.documents.some(d => d.name === f.name));
    if (!incoming.length) { setUploadError('所选文件都已存在。可关闭窗口，在资料详情中重新编译。'); return; }
    setBusy(true);
    schedule(() => {
      const added: Source[] = incoming.map(f => ({ hash: `doc_${crypto.randomUUID().slice(0, 8)}`, name: f.name, display_type: f.name.split('.').pop()!.toUpperCase(), pages: null, phase: 'ready', content: '## 导入流程演示\n此处演示了文件选择与编译完成后的状态。文件内容没有上传或解析；真实资料正文将在服务接入后提供。' }));
      updateBase(name, b => ({ ...b, documents: [...added, ...b.documents] })); setSourceId(added[0].hash); setTab('sources'); setSearch(''); setType('all'); setBusy(false); setUpload(false); setFiles([]); Toast.success(`演示导入完成：新增 ${added.length} 份，跳过 ${files.length - added.length} 份`);
    }, 1600);
  }
  function runLint() { setLintBusy(true); schedule(() => { setLintBusy(false); setLinted(true); setFixed(false); }, 1100); }

  return <div className="page knowledge-page">
    <PageHeading title="我的知识库" description="集中保存资料，整理成方便阅读和查找的知识。"><Button className="secondary-action" type="tertiary" icon={<Plus size={15} />} onClick={() => { setNewName(''); setNewError(''); setCreate(true); }}>新建知识库</Button><Button theme="solid" icon={<Upload size={15} />} onClick={() => { setUploadError(''); setUpload(true); }}>导入资料</Button></PageHeading>
    <div className="kb-banner"><div className="kb-banner-icon"><FolderOpen size={23} strokeWidth={1.4} /></div><div className="kb-banner-main"><Select aria-label="选择知识库" value={baseName} onChange={v => switchBase(String(v))} optionList={bases.map(b => ({ label: b.name, value: b.name }))} /><p>{base.description}</p></div><div className="kb-banner-stat"><strong>{String(base.documents.length).padStart(2, '0')}</strong><span>份资料</span></div><div className="kb-banner-stat"><strong>{String(base.pages.length).padStart(2, '0')}</strong><span>知识页面</span></div><div className="kb-banner-decoration" aria-hidden="true"><span /><span /><span /></div></div>
    <Tabs value={tab} onChange={switchTab} items={[{ id: 'sources', label: '原始资料', count: base.documents.length }, { id: 'pages', label: '知识页面', count: base.pages.length }, { id: 'graph', label: '关联图谱' }, { id: 'quality', label: '质量检查' }]} />
    {(tab === 'sources' || tab === 'pages') && <div className="toolbar"><SearchField value={search} onChange={setSearch} placeholder={tab === 'sources' ? '搜索资料名称…' : '搜索知识页面标题…'} /><Select aria-label="筛选内容类型" value={type} onChange={v => setType(String(v))} optionList={tab === 'sources' ? [{ value: 'all', label: '所有文件类型' }, { value: 'MD', label: 'Markdown' }, { value: 'PDF', label: 'PDF' }, { value: 'TXT', label: 'TXT' }, { value: 'DOCX', label: 'DOCX' }] : [{ value: 'all', label: '所有页面类型' }, ...Object.entries(kindLabel).map(([value, label]) => ({ value, label }))]} /><span className="toolbar-spacer" /><span className="text-muted" style={{ fontSize: 10 }}>{tab === 'sources' ? `${ready} 份资料已就绪` : '从资料中沉淀的知识'}</span></div>}
    {tab === 'sources' && <div className={`workspace-split ${selected ? '' : 'no-detail'}`}><section className="list-panel" aria-label="资料列表"><table className="data-table"><colgroup><col /><col style={{ width: 85 }} /><col className="mobile-hidden" style={{ width: 68 }} /><col className="optional-column" style={{ width: 75 }} /></colgroup><thead><tr><th>资料名称</th><th>处理状态</th><th className="mobile-hidden">格式</th><th className="optional-column">页数</th></tr></thead><tbody>{documents.map(d => <tr key={d.hash} className={d.hash === sourceId ? 'selected' : ''}><td><div className="file-cell"><div className={`file-icon ${d.display_type.toLowerCase()}`}><FileText size={17} strokeWidth={1.5} /></div><div><button className="table-item" aria-label={`查看资料：${d.name}`} onClick={() => setSourceId(d.hash)}>{d.name}</button><div className="item-sub"><span className="mono">{d.hash}</span></div></div></div></td><td><Status tone={d.phase === 'ready' ? 'green' : d.phase === 'failed' ? 'amber' : 'pink'}>{d.phase === 'ready' ? '已就绪' : d.phase === 'failed' ? '需重试' : '编译中'}</Status></td><td className="mobile-hidden text-muted" style={{ fontSize: 10 }}>{d.display_type}</td><td className="optional-column text-muted" style={{ fontSize: 11 }}>{d.pages ?? '—'}</td></tr>)}</tbody></table>{documents.length === 0 && <EmptyState title={base.documents.length ? '没有匹配的资料' : '从第一份资料开始'} description="导入文档，再把它们整理成相互连接的知识。"><Button icon={<Upload size={14} />} onClick={() => setUpload(true)}>导入资料</Button></EmptyState>}<TableFooter count={documents.length} noun="份资料" /><div className="pipeline-strip"><span><Upload size={13} />导入资料</span><ArrowRight size={12} /><span><Network size={13} />编译与关联</span><ArrowRight size={12} /><span><BookOpen size={13} />沉淀为知识</span></div></section>
      {selected && <button className="detail-backdrop" aria-label="返回资料列表" onClick={() => setSourceId(null)} />}{selected && <aside className="detail-panel" aria-label="资料详情"><DetailHeading label="资料详情" onClose={() => setSourceId(null)} /><div className="source-cover"><div className={`document-paper ${selected.display_type.toLowerCase()}`}><span>{selected.display_type}</span><div /><div /><div /><div /><FileText size={18} /></div><span className="cover-label">SOURCE DOCUMENT</span></div><div className="detail-body"><h2 className="source-title">{selected.name}</h2><dl className="detail-properties"><div><dt>处理状态</dt><dd><Status tone={selected.phase === 'failed' ? 'amber' : selected.phase === 'compiling' ? 'pink' : 'green'}>{selected.phase === 'ready' ? '已就绪' : selected.phase === 'failed' ? '编译失败' : '编译中'}</Status></dd></div><div><dt>文件格式</dt><dd>{selected.display_type}</dd></div><div><dt>文档页数</dt><dd>{selected.pages ?? '不适用 / 暂无'}</dd></div></dl>{selected.phase === 'failed' && <div className="callout">示例失败原因：未提取到可读文本。检查文件后可以重新编译。</div>}<div className="detail-actions"><Button icon={<BookOpen size={13} />} onClick={() => setReader({ title: selected.name, content: selected.content })}>阅读原文</Button><Button type="tertiary" disabled={selected.phase === 'compiling'} icon={<RefreshCw size={13} className={selected.phase === 'compiling' ? 'spinning' : ''} />} onClick={() => recompile(selected)}>重新编译</Button></div><div className="detail-section"><h3>生成的知识页面 <span className="text-muted">{related.length.toString().padStart(2, '0')}</span></h3>{related.length ? related.map(p => <button className="related-page" key={p.path} onClick={() => { setPagePath(p.path); switchTab('pages'); }}><BookOpen size={13} /><span>{p.title}</span><ArrowUpRight size={13} /></button>) : <p className="field-hint">此示例资料暂无关联知识页面。</p>}</div><div className="detail-section"><Button type="danger" theme="borderless" icon={<Trash2 size={12} />} style={{ fontSize: 11, padding: 0 }} onClick={() => setDeleteSource(true)}>移除资料</Button></div></div></aside>}
    </div>}
    {(tab === 'pages' || tab === 'graph') && <div className={`workspace-split ${selectedPage ? '' : 'no-detail'}`} style={tab === 'graph' ? { marginTop: 22 } : undefined}><section className="list-panel">{tab === 'graph' && base.pages.length > 0 ? <Graph pages={base.pages} selected={pagePath} onSelect={p => setPagePath(p.path)} /> : tab === 'pages' ? <><table className="data-table"><colgroup><col /><col style={{ width: 76 }} /><col className="mobile-hidden" style={{ width: 90 }} /></colgroup><thead><tr><th>知识页面</th><th>类型</th><th className="mobile-hidden">来源资料</th></tr></thead><tbody>{pages.map(p => <tr key={p.path} className={p.path === pagePath ? 'selected' : ''}><td><button className="table-item" onClick={() => setPagePath(p.path)}>{p.title}</button><div className="item-sub mono">{p.path}</div></td><td><Status tone={p.kind === 'concepts' ? 'green' : p.kind === 'entities' ? 'pink' : 'gray'}>{kindLabel[p.kind]}</Status></td><td className="mobile-hidden text-muted">{p.sources.length} 份</td></tr>)}</tbody></table><TableFooter count={pages.length} noun="个页面" /></> : null}{(tab === 'pages' ? pages : base.pages).length === 0 && <EmptyState title="这里还没有知识页面" description="资料编译后，摘要、概念与实体会汇集在这里。" />}</section>{selectedPage && <button className="detail-backdrop" aria-label="返回知识页面列表" onClick={() => setPagePath('')} />}{selectedPage && <aside className="detail-panel" aria-label="知识页面详情"><DetailHeading label="知识页面" onClose={() => setPagePath('')} /><div className="wiki-preview"><Status>{kindLabel[selectedPage.kind]}</Status><h2>{selectedPage.title}</h2><Reading content={selectedPage.content} /><div className="detail-actions"><Button icon={<Pencil size={13} />} onClick={() => { setPageDraft(selectedPage.content); setPageError(''); setEditingPage(true); }}>编辑页面</Button><Button type="tertiary" icon={<ArrowUpRight size={13} />} onClick={() => setReader({ title: selectedPage.title, content: selectedPage.content })}>展开阅读</Button></div><div className="detail-section"><h3>来源资料</h3>{selectedPage.sources.map(name => <button className="related-page" key={name} onClick={() => { const source = base.documents.find(d => d.name === name); if (source) { setSourceId(source.hash); switchTab('sources'); } }}><FileText size={12} /><span>{name}</span><ArrowUpRight size={12} /></button>)}</div></div></aside>}</div>}
    {tab === 'quality' && <section className="quality-panel"><div className="quality-header"><div className="quality-icon"><ShieldCheck size={29} strokeWidth={1.3} /></div><div><h2>让知识保持可靠</h2><p>检查失效引用、孤立页面与知识内容，查看报告后再决定是否修复。</p></div><Button theme="solid" disabled={lintBusy || base.documents.length === 0} icon={lintBusy ? <Loader2 size={14} className="spinning" /> : <CheckCheck size={14} />} onClick={runLint}>{lintBusy ? '正在检查…' : '运行示例检查'}</Button></div>{!linted ? <EmptyState title={lintBusy ? '正在检查关联与内容' : '还没有检查报告'} description="检查完成后，这里会展示结构检查与知识检查的结果。" /> : <div className="quality-results"><div className="quality-result"><Check size={18} /><div><h3>知识检查</h3><p>本次示例未发现相互矛盾的描述。</p></div><Status>通过</Status></div><div className="quality-result"><CircleAlert size={18} /><div><h3>结构检查</h3><p>{fixed ? '已将示例中的 1 处失效引用降级为普通文本。' : '示例发现 1 处指向旧页面的引用，建议转为普通文本。'}</p></div>{fixed ? <Status>已修复</Status> : <Button onClick={() => { setFixed(true); Toast.success('示例修复完成'); }}>修复失效引用</Button>}</div><p className="field-hint">以上为预设示例报告，不代表对真实资料的检查结果。接入后显示服务返回的结构报告和知识报告。</p></div>}</section>}
    <footer className="bottom-caption"><span>知识由 OpenKB 管理 · 演示未连接服务</span><span>FROM INFORMATION TO UNDERSTANDING ↗</span></footer>
    <Modal title="导入资料" visible={upload} onCancel={() => { if (!busy) setUpload(false); }} onOk={startImport} okText={busy ? '正在演示编译…' : '开始导入'} cancelText="取消" confirmLoading={busy} cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} width={570}>
      <p className="field-hint" style={{ marginBottom: 18 }}>导入到「{baseName}」。本次仅演示导入流程，文件不会上传。</p><input ref={fileInput} type="file" multiple accept=".pdf,.md,.txt,.docx" aria-label="选择资料文件" className="file-input" onChange={e => { if (e.target.files) chooseFiles(e.target.files); e.target.value = ''; }} />
      <button className="upload-zone" disabled={busy} onClick={() => fileInput.current?.click()} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (!busy) chooseFiles(e.dataTransfer.files); }}><Upload size={27} strokeWidth={1.3} /><strong>拖拽文件到这里，或点击选择</strong><span>PDF、Markdown、TXT、DOCX · 单个文件不超过 100 MB</span></button>
      {files.map(f => <div className="upload-file" key={f.name}><FileText size={15} /><span>{f.name}</span>{busy ? <Loader2 size={14} className="spinning" /> : <button aria-label={`移除待导入文件 ${f.name}`} onClick={() => setFiles(items => items.filter(x => x.name !== f.name))}><X size={14} /></button>}</div>)}
      {!files.length && <Button theme="borderless" type="tertiary" style={{ marginTop: 10, fontSize: 12 }} onClick={() => { setFiles([{ name: '协作与设计原则.md', size: 2048 }]); setUploadError(''); }}>没有文件？使用示例文件体验 <ArrowRight size={13} /></Button>}{uploadError && <p role="alert" className="form-error" style={{ marginTop: 13 }}>{uploadError}</p>}{busy && <div className="callout" style={{ marginTop: 14 }}>已接收文件信息 → 正在演示编译 → 更新资料列表</div>}
    </Modal>
    <Modal title="新建知识库" visible={create} onCancel={() => setCreate(false)} okText="创建知识库" cancelText="取消" onOk={() => { const name = newName.trim(); if (!name || /[\\/]/.test(name) || name.startsWith('.')) { setNewError('填写一个名称，不能以点开头或包含路径分隔符。'); return; } if (bases.some(b => b.name === name)) { setNewError('这个知识库名称已存在。'); return; } setBases(items => [...items, { name, description: '从第一份资料开始，慢慢建立你的知识。', documents: [], pages: [] }]); setBaseName(name); setSourceId(null); setPagePath(''); setSearch(''); setType('all'); setTab('sources'); setLinted(false); setFixed(false); setCreate(false); Toast.success('示例知识库已创建'); }}><Field label="知识库名称"><Input aria-label="知识库名称" value={newName} onChange={setNewName} placeholder="例如：产品研究" /></Field>{newError && <p className="form-error" role="alert">{newError}</p>}<p className="field-hint">每个知识库独立管理自己的资料和知识页面。</p></Modal>
    <Modal title={reader?.title} visible={reader !== null} onCancel={() => setReader(null)} footer={<Button onClick={() => setReader(null)}>关闭阅读</Button>} width={780}><Reading content={reader?.content ?? ''} /></Modal>
    <Modal title={`编辑 · ${selectedPage?.title ?? ''}`} visible={editingPage} onCancel={() => setEditingPage(false)} okText="保存页面" cancelText="取消" width={720} onOk={() => { if (!pageDraft.trim()) { setPageError('页面内容不能为空。'); return; } updateBase(baseName, b => ({ ...b, pages: b.pages.map(p => p.path === pagePath ? { ...p, content: pageDraft } : p) })); setEditingPage(false); Toast.success('页面已保存到本次演示'); }}><div className="callout" style={{ marginBottom: 16 }}>可修正正文；后续重新编译原始资料时，生成内容可能被更新。</div><TextArea aria-label="知识页面正文" value={pageDraft} onChange={setPageDraft} autosize={{ minRows: 12, maxRows: 20 }} />{pageError && <p role="alert" className="form-error">{pageError}</p>}</Modal>
    <Modal title="移除这份资料？" visible={deleteSource} onCancel={() => setDeleteSource(false)} okText="确认移除" cancelText="保留资料" okButtonProps={{ type: 'danger' }} onOk={() => { if (!selected) return; updateBase(baseName, b => ({ ...b, documents: b.documents.filter(d => d.hash !== selected.hash), pages: b.pages.map(p => ({ ...p, sources: p.sources.filter(name => name !== selected.name) })).filter(p => p.sources.length > 0) })); setSourceId(null); setDeleteSource(false); Toast.success('已移除示例资料并更新关联页面'); }}><p className="danger-copy">「{selected?.name}」有 {related.length} 个关联知识页面。此演示会移除仅由该资料支持的页面，并更新共同来源。</p><div className="callout" style={{ marginTop: 15 }}>正式接入后，先显示服务返回的删除影响预览，再确认执行。其他资料支持的内容会按服务规则保留。</div></Modal>
  </div>;
}
