import { useEffect, useRef, useState } from 'react';
import { Brain, BookOpen, Palette, Search, ArrowUpRight } from 'lucide-react';
import { BrandMark, Modal, SearchField, navigate, useHashLocation } from '@icarus/ui';
import MemoryApp from '@icarus/memory-app';
import KnowledgeApp from '@icarus/knowledge-app';
import DesignSystem from './DesignSystem';

const routes = [
  { id: 'memory', label: '记忆', english: 'Memory', icon: Brain },
  { id: 'knowledge', label: '知识库', english: 'Knowledge', icon: BookOpen },
  { id: 'design', label: '设计规范', english: 'Design', icon: Palette },
];

export default function App() {
  const hash = useHashLocation();
  const route = hash.split('?')[0].split('/')[1] || 'memory';
  const lastPaths = useRef<Record<string, string>>({ memory: '#/memory/all', knowledge: '#/knowledge/pages', design: '#/design' });
  const [command, setCommand] = useState(false);
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!routes.some(item => item.id === route)) { navigate('#/memory/all', true); return; }
    if (hash) lastPaths.current[route] = hash;
    document.title = `Icarus · ${routes.find(item => item.id === route)?.label}`;
  }, [hash, route]);

  function openNavigation() {
    if (Array.from(document.querySelectorAll('[role="dialog"]')).some(el => el.getClientRects().length > 0)) return;
    setQuery('');
    setCommand(true);
  }

  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        if (command) setCommand(false);
        else openNavigation();
      }
    }
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [command]);

  const matches = routes.filter(item => `${item.label} ${item.english}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="app-layout">
    <main className="shell-main" id="main-content">
      <div hidden={route !== 'memory'}><MemoryApp active={route === 'memory'} hash={route === 'memory' ? hash : lastPaths.current.memory} /></div>
      <div hidden={route !== 'knowledge'}><KnowledgeApp active={route === 'knowledge'} hash={route === 'knowledge' ? hash : lastPaths.current.knowledge} /></div>
      <div hidden={route !== 'design'}><DesignSystem /></div>
    </main>
    <aside className="app-dock" aria-label="Icarus 导航">
      <a className="brand" href={lastPaths.current.memory} aria-label="Icarus 首页"><BrandMark size={27} /></a>
      <nav aria-label="应用导航">{routes.filter(item => item.id !== 'design').map(item => <a
        key={item.id} className={`nav-link ${route === item.id ? 'active' : ''}`}
        href={lastPaths.current[item.id]} aria-current={route === item.id ? 'page' : undefined} aria-label={item.label}
      ><item.icon size={18} strokeWidth={1.7} /><span>{item.label}</span></a>)}</nav>
      <div className="dock-tools">
        <button className="dock-tool" onClick={openNavigation} aria-label="打开快捷导航" title="快捷导航 · Ctrl K"><Search size={18} /></button>
        <a className={`dock-tool ${route === 'design' ? 'active' : ''}`} href="#/design" aria-label="设计规范" title="设计规范" aria-current={route === 'design' ? 'page' : undefined}><Palette size={18} /></a>
      </div>
    </aside>
    <span className="demo-indicator" title="交互演示 · 未连接服务，刷新恢复示例数据"><i />演示空间</span>
    <Modal title="快捷导航" visible={command} onCancel={() => setCommand(false)} footer={null} width={460}>
      <div className="command-search"><SearchField value={query} onChange={setQuery} placeholder="搜索页面名称…" /></div>
      <div className="command-list">{matches.map(item => <button key={item.id} onClick={() => { navigate(lastPaths.current[item.id]); setCommand(false); }}><span><item.icon size={17} />{item.label}<small>/ {item.english}</small></span><ArrowUpRight size={15} /></button>)}</div>
      {!matches.length && <p className="field-hint">没有匹配的页面</p>}
    </Modal>
  </div>;
}
