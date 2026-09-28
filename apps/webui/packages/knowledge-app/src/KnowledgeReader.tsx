import { useState } from 'react';
import { ArrowLeft, ArrowUpRight, BookOpen, FileText, Pencil, RefreshCw, Trash2, Check, Link2 } from 'lucide-react';
import { Button, TextArea, Status, EmptyState } from '@icarus/ui';
import { kindLabel, type KnowledgeBase, type Source, type WikiPage } from './demo';
import Reading from './Reading';

type Props = {
  base: KnowledgeBase;
  page?: WikiPage;
  source?: Source;
  backLabel: string;
  onBack: () => void;
  onPage: (page: WikiPage) => void;
  onSource: (source: Source) => void;
  onSave: (path: string, content: string) => void;
  onRetry: (source: Source) => void;
  onDelete: (source: Source) => void;
};

export default function KnowledgeReader({ base, page, source, backLabel, onBack, onPage, onSource, onSave, onRetry, onDelete }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(page?.content ?? '');
  const [error, setError] = useState('');
  const title = page?.title ?? source?.name ?? '';
  const related = source ? base.pages.filter(item => item.sources.includes(source.name)) : [];

  function save() {
    if (!page) return;
    if (!draft.trim()) { setError('页面内容不能为空。'); return; }
    onSave(page.path, draft.trim()); setEditing(false);
  }

  return <section className="reader-workspace" aria-label={page ? '知识页面详情' : '资料详情'}>
    <div className="reader-topline"><button className="link-button reader-back" onClick={onBack}><ArrowLeft size={15} />{backLabel}</button><span>{page ? '知识页面' : '原始资料'}</span></div>
    <div className="reader-layout">
      <aside className="reader-index" aria-label={page ? '知识页面索引' : '资料索引'}>
        <h2>{page ? '知识页面' : '资料'}<span>{page ? base.pages.length : base.documents.length}</span></h2>
        {page ? base.pages.map(item => <button key={item.path} className={item.path === page.path ? 'active' : ''} onClick={() => onPage(item)} aria-current={item.path === page.path ? 'page' : undefined}><BookOpen size={14} /><span>{item.title}</span></button>) : base.documents.map(item => <button key={item.hash} className={item.hash === source?.hash ? 'active' : ''} onClick={() => onSource(item)} aria-current={item.hash === source?.hash ? 'page' : undefined}><FileText size={14} /><span>{item.name}</span></button>)}
      </aside>
      <article className="reading-paper">
        <header className="reading-header">
          <div className="reading-eyebrow">{page ? <><BookOpen size={15} />{kindLabel[page.kind]}</> : <><FileText size={15} />{source?.display_type}{source?.pages != null && <span>· {source.pages} 页</span>}</>}</div>
          <h2>{title}</h2>
          <div className="reading-actions">
            {page ? <><span className="source-count"><Link2 size={13} />{page.sources.length} 份来源</span><span className="toolbar-spacer" />{!editing && <Button type="tertiary" theme="borderless" icon={<Pencil size={14} />} onClick={() => { setDraft(page.content); setError(''); setEditing(true); }}>编辑页面</Button>}</> : source && <>
              <Status tone={source.phase === 'failed' ? 'amber' : source.phase === 'compiling' ? 'blue' : 'gray'}>{source.phase === 'failed' ? '需重试' : source.phase === 'compiling' ? '编译中' : source.content ? '已就绪' : '未解析'}</Status>
              <span className="toolbar-spacer" />
              <Button type="tertiary" theme="borderless" disabled={source.phase === 'compiling'} icon={<RefreshCw size={14} className={source.phase === 'compiling' ? 'spinning' : ''} />} onClick={() => onRetry(source)}>重新编译</Button>
              <Button className="icon-button" type="danger" theme="borderless" icon={<Trash2 size={15} />} aria-label="移除资料" title="移除资料" onClick={() => onDelete(source)} />
            </>}
          </div>
        </header>
        {source?.phase === 'failed' && <div className="source-error">未提取到可读文本。检查文件后可重新编译。</div>}
        {editing ? <div className="page-editor" onKeyDown={event => {
          if (event.key === 'Escape') { event.stopPropagation(); setEditing(false); }
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); save(); }
        }}><TextArea aria-label="知识页面正文" value={draft} onChange={setDraft} autosize={{ minRows: 14, maxRows: 32 }} autoFocus />
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="page-editor-footer"><span>Markdown · Ctrl ↵ 保存</span><Button type="tertiary" theme="borderless" onClick={() => setEditing(false)}>取消编辑</Button><Button theme="solid" icon={<Check size={14} />} onClick={save}>保存页面</Button></div>
          <p className="field-hint">重新编译来源资料后，生成内容可能更新。</p>
        </div> : page?.content || source?.content ? <Reading content={page?.content ?? source?.content ?? ''} /> : <EmptyState title="正文尚未解析" description="当前导入仅演示流程，文件未上传。" />}
        {!editing && <section className="reading-references" aria-label={page ? '来源资料' : '关联知识页面'}>
          <h3>{page ? '来源资料' : '关联知识页面'}<span>{page ? page.sources.length : related.length}</span></h3>
          {page ? page.sources.map(name => {
            const document = base.documents.find(item => item.name === name);
            return document ? <button className="reference-link" key={name} onClick={() => onSource(document)}><FileText size={16} /><span>{name}<small>{document.display_type}{document.pages != null ? ` · ${document.pages} 页` : ''}</small></span><ArrowUpRight size={15} /></button> : <div className="reference-missing" key={name}>{name}<Status tone="amber">来源已移除</Status></div>;
          }) : related.length ? related.map(item => <button className="reference-link" key={item.path} onClick={() => onPage(item)}><BookOpen size={16} /><span>{item.title}<small>{kindLabel[item.kind]}</small></span><ArrowUpRight size={15} /></button>) : <p className="field-hint">暂无关联知识页面</p>}
        </section>}
      </article>
    </div>
  </section>;
}
