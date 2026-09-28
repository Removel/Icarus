import { useEffect, useState } from 'react';
import { Brain, BookOpen, Palette, Search, ChevronRight, ArrowUpRight } from 'lucide-react';
import { BrandMark, Modal, SearchField } from '@icarus/ui';
import MemoryApp from '@icarus/memory-app';
import KnowledgeApp from '@icarus/knowledge-app';
import DesignSystem from './DesignSystem';

const routes = [
  { id: 'memory', label: '记忆', english: 'Memory', icon: Brain, sections: [{ id: 'all', label: '全部记忆' }, { id: 'active', label: '生效中' }, { id: 'expired', label: '已失效' }] },
  { id: 'knowledge', label: '知识库', english: 'Knowledge', icon: BookOpen, sections: [{ id: 'sources', label: '资料' }, { id: 'pages', label: '知识页面' }, { id: 'graph', label: '关联图谱' }, { id: 'quality', label: '质量检查' }] },
  { id: 'design', label: '设计规范', english: 'Design', icon: Palette, sections: [] },
];

function currentRoute() {
  const id = location.hash.replace('#/', '').split('/')[0];
  return routes.some(route => route.id === id) ? id : 'memory';
}

export default function App() {
  const [path, setPath] = useState(location.hash);
  const route = currentRoute();
  const [command, setCommand] = useState(false);
  const [query, setQuery] = useState('');
  useEffect(() => {
    function handleRoute() { setPath(location.hash); document.querySelector('.shell-main')?.scrollTo({ top: 0 }); }
    function keyboard(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCommand(value => !value); } }
    window.addEventListener('hashchange', handleRoute);
    window.addEventListener('keydown', keyboard);
    return () => { window.removeEventListener('hashchange', handleRoute); window.removeEventListener('keydown', keyboard); };
  }, []);
  useEffect(() => { document.title = `Icarus · ${routes.find(item => item.id === route)?.label}`; }, [route]);
  const section = path.replace('#/', '').split('/')[1];
  const current = routes.find(item => item.id === route)!;
  return <div className="app-layout">
    <aside className="sidebar" aria-label="Icarus 导航">
      <a className="brand" href="#/memory" aria-label="Icarus 首页"><BrandMark /><span className="brand-word">icarus</span></a>
      <nav aria-label="应用导航">
        <div className="nav-label">我的空间</div>
        {routes.map(item => <div className="nav-group" key={item.id}>
          <a className={`nav-link ${route === item.id ? 'active' : ''}`} href={`#/${item.id}`} aria-current={route === item.id ? 'page' : undefined} aria-label={item.label} title={item.label}><item.icon size={18} strokeWidth={1.8} /><span>{item.label}</span></a>
          {route === item.id && item.sections.length > 0 && <div className="subnav" aria-label={`${item.label}分类`}>{item.sections.map(entry => <a key={entry.id} href={`#/${item.id}/${entry.id}`} className={`subnav-link ${section === entry.id || (!section && entry.id === item.sections[0].id) ? 'active' : ''}`}>{entry.label}</a>)}</div>}
        </div>)}
      </nav>
      <button className="dock-search" onClick={() => { setQuery(''); setCommand(true); }} aria-label="打开快捷导航" title="快速前往"><Search size={18} /></button>
      <div className="sidebar-bottom"><div className="profile"><div className="profile-icon">L</div><div>Lin<small>演示工作区</small></div></div></div>
    </aside>
    <main className="shell-main">
      <header className="topbar"><div className="breadcrumb"><span>我的空间</span><ChevronRight size={14} /><strong>{current.label}</strong></div><div className="topbar-end"><span className="demo-label">交互演示</span><button className="command-trigger" onClick={() => { setQuery(''); setCommand(true); }} aria-label="打开快捷导航"><Search size={16} /><span>快速前往</span><kbd>Ctrl K</kbd></button></div></header>
      <div hidden={route !== 'memory'}><MemoryApp /></div>
      <div hidden={route !== 'knowledge'}><KnowledgeApp /></div>
      <div hidden={route !== 'design'}><DesignSystem /></div>
    </main>
    <Modal title="快速前往" visible={command} onCancel={() => setCommand(false)} footer={null} width={480}><SearchField value={query} onChange={setQuery} placeholder="搜索页面名称…" /><div className="command-list">{routes.filter(item => `${item.label} ${item.english}`.toLowerCase().includes(query.toLowerCase())).map(item => <button key={item.id} onClick={() => { location.hash = `/${item.id}`; setCommand(false); }}><span>{item.label} <small className="text-muted">/ {item.english}</small></span><ArrowUpRight size={15} /></button>)}{!routes.some(item => `${item.label} ${item.english}`.toLowerCase().includes(query.toLowerCase())) && <p className="field-hint">没有匹配的页面。</p>}</div></Modal>
  </div>;
}
