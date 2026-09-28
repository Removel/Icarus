import { useEffect, useRef, useState } from 'react';
import { Plus, Upload, FileText, BookOpen, ArrowUpRight, RefreshCw, X, Link2, ShieldCheck, CircleAlert, Check, Loader2 } from 'lucide-react';
import { Button, Select, Modal, Input, Toast, PageHeading, Status, Tabs, SearchField, Field, EmptyState, navigate } from '@icarus/ui';
import { initialBases, kindLabel, type KnowledgeBase, type Source, type WikiPage } from './demo';
import Graph from './Graph';
import KnowledgeReader from './KnowledgeReader';
import { excerpt } from './Reading';
import './knowledge.css';

type Destination = { baseName?: string; pagePath?: string; sourceId?: string; from?: string };
const sourceState = (source: Source) => source.phase === 'failed' ? '需重试' : source.phase === 'compiling' ? '编译中' : source.content ? '已就绪' : '未解析';

export default function KnowledgeApp({ active, hash }: { active: boolean; hash: string }) {
  const [bases, setBases] = useState<KnowledgeBase[]>(initialBases);
  const params = new URLSearchParams(hash.split('?')[1]);
  const section = hash.split('?')[0].split('/')[2];
  const view = ['pages', 'sources', 'graph'].includes(section) ? section : 'pages';
  const requestedBase = params.get('base');
  const base = bases.find(item => item.name === requestedBase) ?? bases[0];
  const sourceId = params.get('source');
  const pagePath = params.get('page');
  const fromPage = params.get('from');
  const source = view === 'sources' ? base.documents.find(item => item.hash === sourceId) : undefined;
  const page = view !== 'sources' ? base.pages.find(item => item.path === pagePath) : undefined;
  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [upload, setUpload] = useState(false);
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);
  const [uploadError, setUploadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [create, setCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newError, setNewError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ baseName: string; source: Source } | null>(null);
  const [quality, setQuality] = useState(false);
  const [report, setReport] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const readerRef = useRef<HTMLDivElement>(null);
  const importing = useRef(false);

  function go(nextView = view, destination: Destination = {}, replace = false) {
    const query = new URLSearchParams({ base: destination.baseName ?? base.name });
    if (destination.pagePath) query.set('page', destination.pagePath);
    if (destination.sourceId) query.set('source', destination.sourceId);
    if (destination.from) query.set('from', destination.from);
    navigate(`#/knowledge/${nextView}?${query}`, replace);
  }

  useEffect(() => {
    if (!active) return;
    if (section === 'quality') { setQuality(true); setReport(false); go('pages', {}, true); return; }
    if ((requestedBase && !bases.some(item => item.name === requestedBase)) || (sourceId && !source) || (pagePath && !page)) go(view, {}, true);
  }, [active, hash, bases]);

  useEffect(() => {
    if (!active) { setUpload(false); setCreate(false); setDeleteTarget(null); setQuality(false); }
  }, [active]);
  useEffect(() => { setSearch(''); setType('all'); setQuality(false); setReport(false); }, [base.name, view]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  useEffect(() => {
    if (active && (sourceId || pagePath)) {
      readerRef.current?.scrollIntoView({ block: 'start' });
      readerRef.current?.focus({ preventScroll: true });
    }
  }, [active, sourceId, pagePath, base.name]);

  const documents = base.documents.filter(item => (type === 'all' || item.display_type === type) && item.name.toLowerCase().includes(search.trim().toLowerCase()));
  const pages = base.pages.filter(item => (type === 'all' || item.kind === type) && `${item.title} ${item.content}`.toLowerCase().includes(search.trim().toLowerCase()));
  const deleteBase = deleteTarget && bases.find(item => item.name === deleteTarget.baseName);
  const deleteRelated = deleteBase?.pages.filter(item => item.sources.includes(deleteTarget!.source.name)) ?? [];

  function updateBase(name: string, update: (item: KnowledgeBase) => KnowledgeBase) {
    setBases(items => items.map(item => item.name === name ? update(item) : item));
  }

  function schedule(callback: () => void, ms: number) { timers.current.push(setTimeout(callback, ms)); }

  function recompile(document: Source) {
    if (document.phase === 'compiling') return;
    const name = base.name;
    updateBase(name, item => ({ ...item, documents: item.documents.map(entry => entry.hash === document.hash ? { ...entry, phase: 'compiling' } : entry) }));
    schedule(() => {
      updateBase(name, item => ({ ...item, documents: item.documents.map(entry => entry.hash === document.hash ? { ...entry, phase: 'ready' } : entry) }));
      Toast.info('演示编译完成，未调用解析服务');
    }, 900);
  }

  function chooseFiles(incoming: FileList | File[]) {
    const list = Array.from(incoming);
    if (list.some(file => !/\.(pdf|md|txt|docx)$/i.test(file.name))) { setUploadError('支持 PDF、Markdown、TXT 和 DOCX 文件。'); return; }
    const unique = [...files, ...list.map(file => ({ name: file.name, size: file.size }))].filter((file, index, all) => all.findIndex(item => item.name === file.name) === index);
    if (unique.some(file => file.size > 100 * 1024 * 1024) || unique.reduce((size, file) => size + file.size, 0) > 500 * 1024 * 1024) {
      setUploadError('单个文件不超过 100 MB，单次合计不超过 500 MB。'); return;
    }
    setFiles(unique); setUploadError('');
  }

  function startImport() {
    if (importing.current) return;
    if (!files.length) { setUploadError('请先选择文件。'); return; }
    const incoming = files.filter(file => !base.documents.some(item => item.name === file.name));
    if (!incoming.length) { setUploadError('所选文件已存在，可在资料中重新编译。'); return; }
    const name = base.name;
    const origin = window.location.hash;
    const skipped = files.length - incoming.length;
    importing.current = true; setBusy(true);
    schedule(() => {
      const added: Source[] = incoming.map(file => ({ hash: `doc_${crypto.randomUUID().slice(0, 8)}`, name: file.name, display_type: file.name.split('.').pop()!.toUpperCase(), pages: null, phase: 'ready', content: '' }));
      updateBase(name, item => ({ ...item, documents: [...added, ...item.documents] }));
      setBusy(false); importing.current = false; setUpload(false); setFiles([]);
      if (window.location.hash === origin) go('sources', { baseName: name, sourceId: added[0].hash });
      Toast.success(`已登记 ${added.length} 份演示资料${skipped ? `，跳过 ${skipped} 份重复文件` : ''}`);
    }, 1000);
  }

  function createBase() {
    const name = newName.trim();
    if (!name || /[\\/]/.test(name) || name.startsWith('.')) { setNewError('名称不能为空，不能以点开头或包含路径分隔符。'); return; }
    if (bases.some(item => item.name === name)) { setNewError('这个知识库名称已存在。'); return; }
    setBases(items => [...items, { name, description: '', documents: [], pages: [] }]);
    setCreate(false); go('pages', { baseName: name }); Toast.success('已创建知识库');
  }

  function openPage(item: WikiPage) { go(view === 'graph' ? 'graph' : 'pages', { pagePath: item.path }); }
  function openSource(item: Source) { go('sources', { sourceId: item.hash, from: page?.path ?? fromPage ?? '' }); }
  function closeReader() {
    if (fromPage && base.pages.some(item => item.path === fromPage)) go('pages', { pagePath: fromPage });
    else go(view);
  }

  return <div className="page knowledge-page">
    <PageHeading title="知识库" context={<div className="knowledge-context"><span className="context-divider" /><Select className="base-switch" aria-label="选择知识库" value={base.name} onChange={value => go(view, { baseName: String(value) })} optionList={bases.map(item => ({ label: item.name, value: item.name }))} /></div>}>
      <Button className="icon-button" type="tertiary" theme="borderless" icon={<Plus size={18} />} aria-label="新建知识库" title="新建知识库" onClick={() => { setNewName(''); setNewError(''); setCreate(true); }} />
      <Button theme="solid" icon={<Upload size={16} />} disabled={busy} onClick={() => { setUploadError(''); setUpload(true); }}>{busy ? '导入中…' : '导入资料'}</Button>
    </PageHeading>
    <div className="knowledge-nav"><Tabs value={view} onChange={id => go(id)} items={[{ id: 'pages', label: '知识页面', count: base.pages.length }, { id: 'sources', label: '原始资料', count: base.documents.length }, { id: 'graph', label: '来源关联' }]} /><Button className="quality-trigger" type="tertiary" theme="borderless" icon={<ShieldCheck size={16} />} onClick={() => { setQuality(true); setReport(false); }}>质量检查</Button></div>
    {source || page ? <div ref={readerRef} tabIndex={-1} className="reader-anchor"><KnowledgeReader key={`${base.name}-${source?.hash ?? page?.path}`}
      base={base} source={source} page={page} onBack={closeReader}
      backLabel={fromPage ? '返回知识页面' : view === 'graph' ? '返回来源关联' : source ? '返回资料列表' : '返回知识页面列表'}
      onPage={openPage} onSource={openSource} onRetry={recompile} onDelete={document => setDeleteTarget({ baseName: base.name, source: document })}
      onSave={(path, content) => { const name = base.name; updateBase(name, item => ({ ...item, pages: item.pages.map(entry => entry.path === path ? { ...entry, content } : entry) })); Toast.success('已保存页面'); }}
    /></div> : <>
      {view !== 'graph' && <div className="toolbar">
        <SearchField value={search} onChange={setSearch} placeholder={view === 'sources' ? '搜索资料名称…' : '搜索知识内容…'} />
        <Select aria-label="筛选内容类型" value={type} onChange={value => setType(String(value))} optionList={view === 'sources' ? [{ value: 'all', label: '全部格式' }, ...['MD', 'PDF', 'TXT', 'DOCX'].map(value => ({ value, label: value === 'MD' ? 'Markdown' : value }))] : [{ value: 'all', label: '全部类型' }, ...Object.entries(kindLabel).map(([value, label]) => ({ value, label }))]} />
        {(search || type !== 'all') && <button className="link-button" onClick={() => { setSearch(''); setType('all'); }}>清除筛选<X size={12} /></button>}
      </div>}
      {view === 'pages' && <section aria-label="知识页面列表">
        {pages.length ? <div className="knowledge-grid">{pages.map(item => <article className="knowledge-card" key={item.path}>
          <button className="knowledge-card-main" aria-label={`阅读知识：${item.title}`} onClick={() => openPage(item)}>
            <div className="knowledge-card-top"><span className="knowledge-kind"><BookOpen size={14} />{kindLabel[item.kind]}</span><ArrowUpRight size={17} /></div>
            <h2>{item.title}</h2><p>{excerpt(item.content)}</p>
          </button>
          <footer className="knowledge-card-footer"><Link2 size={13} /><span title={item.sources.join('、')}>{item.sources[0] ?? '暂无来源'}{item.sources.length > 1 ? ` 等 ${item.sources.length} 份资料` : ''}</span></footer>
        </article>)}</div> : <EmptyState title={base.pages.length ? '没有匹配的知识页面' : base.documents.length ? '还没有知识页面' : '从第一份资料开始'} description={!base.pages.length && base.documents.length ? '资料尚未生成知识页面。当前演示不调用解析服务。' : undefined}>
          {base.pages.length ? <Button onClick={() => { setSearch(''); setType('all'); }}>清除筛选</Button> : <Button icon={<Upload size={15} />} onClick={() => { setUploadError(''); setUpload(true); }}>导入资料</Button>}
        </EmptyState>}
      </section>}
      {view === 'sources' && <section className="source-list" aria-label="资料列表">
        {documents.length ? <table className="source-table"><thead><tr><th>资料名称</th><th>关联知识</th><th>处理状态</th><th><span className="visually-hidden">操作</span></th></tr></thead><tbody>{documents.map(document => {
          const count = base.pages.filter(item => item.sources.includes(document.name)).length;
          return <tr key={document.hash}>
            <td><button className="source-open" aria-label={`查看资料：${document.name}`} onClick={() => openSource(document)}><span className={`file-badge file-${document.display_type.toLowerCase()}`}><FileText size={19} strokeWidth={1.5} /></span><span><strong>{document.name}</strong><small>{document.display_type}{document.pages != null ? ` · ${document.pages} 页` : ''}</small></span></button></td>
            <td className="source-relations-count"><span>{count ? `${count} 个页面` : '—'}</span></td>
            <td><Status tone={document.phase === 'failed' ? 'amber' : document.phase === 'compiling' ? 'blue' : 'gray'}>{sourceState(document)}</Status></td>
            <td>{document.phase === 'failed' ? <Button className="icon-button" type="tertiary" theme="borderless" icon={<RefreshCw size={15} />} aria-label={`重新编译：${document.name}`} title="重新编译" onClick={() => recompile(document)} /> : <Button className="icon-button" type="tertiary" theme="borderless" icon={<ArrowUpRight size={16} />} aria-label={`打开原文：${document.name}`} title="打开原文" onClick={() => openSource(document)} />}</td>
          </tr>;
        })}</tbody></table> : <EmptyState title={base.documents.length ? '没有匹配的资料' : '从第一份资料开始'}>{base.documents.length ? <Button onClick={() => { setSearch(''); setType('all'); }}>清除筛选</Button> : <Button icon={<Upload size={15} />} onClick={() => { setUploadError(''); setUpload(true); }}>导入资料</Button>}</EmptyState>}
      </section>}
      {view === 'graph' && <Graph pages={base.pages} sourceNames={base.documents.map(document => document.name)} onSelect={openPage} />}
    </>}
    <Modal title="导入资料" visible={upload} onCancel={() => { if (!busy) setUpload(false); }} onOk={startImport} okText={busy ? '正在导入…' : '开始导入'} confirmLoading={busy} cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} width={540}>
      <p className="import-destination">导入到 <strong>{base.name}</strong></p>
      <input ref={fileInput} type="file" multiple accept=".pdf,.md,.txt,.docx" aria-label="选择资料文件" className="visually-hidden" onChange={event => { if (event.target.files) chooseFiles(event.target.files); event.target.value = ''; }} />
      <button className="upload-zone" disabled={busy} onClick={() => fileInput.current?.click()} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (!busy) chooseFiles(event.dataTransfer.files); }}><span className="upload-icon"><Upload size={24} strokeWidth={1.5} /></span><strong>拖入文件，或点击选择</strong><span>PDF · Markdown · TXT · DOCX</span><small>单个不超过 100 MB · 合计不超过 500 MB</small></button>
      {files.map(file => <div className="upload-file" key={file.name}><FileText size={16} /><span>{file.name}<small>{Math.max(1, Math.round(file.size / 1024))} KB</small></span>{busy ? <Loader2 size={15} className="spinning" /> : <Button className="icon-button" theme="borderless" type="tertiary" aria-label={`移除待导入文件 ${file.name}`} icon={<X size={15} />} onClick={() => setFiles(items => items.filter(item => item.name !== file.name))} />}</div>)}
      {!files.length && <button className="link-button sample-file" onClick={() => { setFiles([{ name: '协作与设计原则.md', size: 2048 }]); setUploadError(''); }}>使用示例文件<ArrowUpRight size={13} /></button>}
      {uploadError && <p className="form-error" role="alert">{uploadError}</p>}
      <p className="field-hint">仅演示导入流程，文件不会上传或解析。</p>
    </Modal>
    <Modal title="新建知识库" visible={create} onCancel={() => setCreate(false)} onOk={createBase} okText="创建知识库" width={440}>
      <Field label="知识库名称"><Input aria-label="知识库名称" value={newName} onChange={setNewName} placeholder="例如：产品研究" /></Field>
      {newError && <p className="form-error" role="alert">{newError}</p>}
    </Modal>
    <Modal title="移除这份资料？" visible={deleteTarget !== null} onCancel={() => setDeleteTarget(null)} okText="确认移除" cancelText="保留资料" okButtonProps={{ type: 'danger' }} onOk={() => {
      if (!deleteTarget) return;
      const { baseName, source: target } = deleteTarget;
      updateBase(baseName, item => ({ ...item, documents: item.documents.filter(document => document.hash !== target.hash), pages: item.pages.map(entry => ({ ...entry, sources: entry.sources.filter(name => name !== target.name) })).filter(entry => entry.sources.length > 0) }));
      if (base.name === baseName && sourceId === target.hash) go('sources', {}, true);
      setDeleteTarget(null); Toast.success('已移除资料并更新关联页面');
    }}><p className="danger-copy">{deleteTarget?.source.name}</p><div className="delete-impact"><div><span>受影响的知识页面</span><strong>{deleteRelated.length}</strong></div><div><span>仅由此资料支持，将一并移除</span><strong>{deleteRelated.filter(item => item.sources.length === 1).length}</strong></div></div><p className="field-hint">以上为本次演示数据的删除影响；其他资料支持的页面会保留。</p></Modal>
    <Modal title="质量检查" visible={quality} onCancel={() => setQuality(false)} footer={<Button onClick={() => setQuality(false)}>关闭</Button>} width={540}>
      <div className="quality-notice"><ShieldCheck size={22} /><div><h3>检查服务尚未连接</h3><p>示例报告不会检查或修改当前资料。</p></div></div>
      {!report ? <Button onClick={() => setReport(true)}>查看示例报告</Button> : <div className="quality-results" aria-label="示例检查报告"><span className="report-label">预设示例</span><div className="quality-result"><Check size={18} /><div><h3>内容一致性</h3><p>示例：未发现矛盾描述</p></div></div><div className="quality-result"><CircleAlert size={18} /><div><h3>引用完整性</h3><p>示例：1 处引用指向已移除页面</p></div><Status tone="amber">待处理</Status></div></div>}
    </Modal>
  </div>;
}
