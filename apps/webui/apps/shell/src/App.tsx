import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ArrowLeft, FileText, NotebookText, Network, ShieldCheck } from 'lucide-react';
import { LoadingIndicator, navigate, useHashLocation } from '@icarus/ui';
import { useTheme } from './theme';
const MemoryApp = lazy(() => import('@icarus/memory-app'));
const KnowledgeApp = lazy(() => import('@icarus/knowledge-app'));
const ChatApp = lazy(() => import('@icarus/chat-app'));

export default function App() {
  const [theme, setTheme] = useTheme();
  const hash = useHashLocation();
  const [memoryHeader, setMemoryHeader] = useState<HTMLDivElement | null>(null);
  const requested = hash.split('?')[0].split('/')[1];
  const route = requested === 'memory' || requested === 'knowledge' ? requested : 'chat';
  const visited = useRef(new Set([route]));
  visited.current.add(route);
  const paths = useRef({
    memory: '#/memory/all',
    knowledge: '#/knowledge/sources',
    chat: '#/chat',
  });
  if (hash && requested === route) paths.current[route] = hash;
  useEffect(() => {
    if (!['chat', 'memory', 'knowledge'].includes(requested)) navigate('#/chat', true);
    document.title = `Icarus · ${route === 'chat' ? '对话' : route === 'memory' ? '记忆' : '知识库'}`;
  }, [requested, route]);
  const knowledgeParams = new URLSearchParams(paths.current.knowledge.split('?')[1]);
  const knowledgeSection =
    (knowledgeParams.get('origin') ?? paths.current.knowledge).split('?')[0].split('/')[2] ||
    'sources';
  const sections = [
    { id: 'sources', label: '原始资料', icon: FileText },
    { id: 'pages', label: '知识页面', icon: NotebookText },
    { id: 'graph', label: '文档关联', icon: Network },
    { id: 'quality', label: '质量检查', icon: ShieldCheck },
  ];
  const management = (
    <>
      <header className="management-header">
        <div className="management-heading">
          <a
            href={paths.current.chat}
            aria-label="返回对话"
            title="返回对话"
            onClick={(event) => {
              if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              navigate(paths.current.chat);
            }}
          >
            <ArrowLeft size={16} />
          </a>
          <span className="management-title">{route === 'memory' ? '记忆' : '知识库'}</span>
        </div>
        <div className="management-header-slot" ref={setMemoryHeader} hidden={route !== 'memory'} />
        <nav aria-label="知识库分类" hidden={route !== 'knowledge'}>
          {sections.map((item) => {
            const params = new URLSearchParams();
            const base = knowledgeParams.get('base');
            if (base) params.set('base', base);
            const target = '#/knowledge/' + item.id + (params.size ? '?' + params : '');
            return (
              <a
                key={item.id}
                href={target}
                aria-current={knowledgeSection === item.id ? 'page' : undefined}
                onClick={(event) => {
                  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  navigate(target);
                }}
              >
                <item.icon size={16} />
                <span>{item.label}</span>
              </a>
            );
          })}
        </nav>
      </header>
      <div className="management-body" hidden={route !== 'memory'}>
        {visited.current.has('memory') && (
          <Suspense fallback={<LoadingIndicator label="正在加载记忆" />}>
            <MemoryApp
              headerContainer={memoryHeader}
              workspace={
                new URLSearchParams(paths.current.chat.split('?')[1]).get('workspace') ?? ''
              }
              active={route === 'memory'}
              hash={route === 'memory' ? hash : paths.current.memory}
            />
          </Suspense>
        )}
      </div>
      <div className="management-body" hidden={route !== 'knowledge'}>
        {visited.current.has('knowledge') && (
          <Suspense fallback={<LoadingIndicator label="正在加载知识库" />}>
            <KnowledgeApp
              active={route === 'knowledge'}
              hash={route === 'knowledge' ? hash : paths.current.knowledge}
            />
          </Suspense>
        )}
      </div>
    </>
  );
  return (
    <div className="app-layout unified-workspace">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
      >
        跳转到主内容
      </a>
      <main className="shell-main" id="main-content" tabIndex={-1}>
        <Suspense fallback={<LoadingIndicator label="正在加载工作台" />}>
          <ChatApp
            active={route === 'chat'}
            hash={route === 'chat' ? hash : paths.current.chat}
            theme={theme}
            onThemeChange={setTheme}
            managementPage={route === 'chat' ? undefined : route}
            managementContent={management}
            managementPaths={{ memory: paths.current.memory, knowledge: paths.current.knowledge }}
            onOpenManagement={(destination) => navigate(paths.current[destination])}
          />
        </Suspense>
      </main>
    </div>
  );
}
