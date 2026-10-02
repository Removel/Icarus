import { lazy, Suspense, useEffect, useRef, useState, type MouseEvent } from 'react';
import {
  Brain,
  BookOpen,
  MessageSquare,
  FileText,
  Network,
  ShieldCheck,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { BrandMark, Button, Layout, navigate, useHashLocation } from '@icarus/ui';
import MemoryApp from '@icarus/memory-app';
import KnowledgeApp from '@icarus/knowledge-app';
const ChatApp = lazy(() => import('@icarus/chat-app'));

const routes = [
  { id: 'memory', label: '记忆', english: 'Memory', icon: Brain },
  { id: 'knowledge', label: '知识库', english: 'Knowledge', icon: BookOpen },
  { id: 'chat', label: '对话', english: 'Chat', icon: MessageSquare },
];

export default function App() {
  const hash = useHashLocation();
  const route = hash.split('?')[0].split('/')[1] || 'memory';
  const lastPaths = useRef<Record<string, string>>({
    memory: '#/memory/all',
    knowledge: '#/knowledge/sources',
    chat: '#/chat',
  });
  const [chatOpened, setChatOpened] = useState(false);
  useEffect(() => {
    if (route === 'chat') setChatOpened(true);
  }, [route]);
  const knowledgeParams = new URLSearchParams(
    (route === 'knowledge' ? hash : lastPaths.current.knowledge).split('?')[1],
  );
  const knowledgeSection =
    (knowledgeParams.get('origin') ?? hash).split('?')[0].split('/')[2] || 'sources';
  const knowledgeLinks = [
    { id: 'sources', label: '原始资料', icon: FileText },
    { id: 'pages', label: '知识页面', icon: BookOpen },
    { id: 'graph', label: '文档关联', icon: Network },
    { id: 'quality', label: '质量检查', icon: ShieldCheck },
  ];
  function knowledgePath(section: string) {
    const params = new URLSearchParams();
    for (const key of ['base', 'preview']) {
      const value = knowledgeParams.get(key);
      if (value) params.set(key, value);
    }
    return '#/knowledge/' + section + (params.size ? '?' + params : '');
  }
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!routes.some((item) => item.id === route)) {
      navigate('#/memory/all', true);
      return;
    }
    if (hash) lastPaths.current[route] = hash;
    document.title = `Icarus · ${routes.find((item) => item.id === route)?.label}`;
  }, [hash, route]);

  function followLink(event: MouseEvent<HTMLAnchorElement>) {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    navigate(event.currentTarget.hash);
  }
  return (
    <Layout className={`app-layout${collapsed ? ' is-collapsed' : ''}`}>
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
      <Layout.Header className="app-header" {...{ role: 'banner' }}>
        <a
          className="brand"
          href={lastPaths.current.memory}
          onClick={followLink}
          aria-label="Icarus 首页"
        >
          <BrandMark size={28} />
          <span>Icarus</span>
        </a>
        <div className="workspace-location">
          <span>工作台</span>
          <span aria-hidden="true">/</span>
          <strong>{routes.find((item) => item.id === route)?.label}</strong>
        </div>
      </Layout.Header>
      <Layout className="workspace-layout" hasSider>
        <Layout.Sider className="app-sidebar" role="complementary" aria-label="Icarus 导航">
          <nav id="app-navigation" aria-label="应用导航">
            {routes.map((item) => (
              <div key={item.id} className={`nav-group nav-group-${item.id}`}>
                <a
                  className={`nav-link ${route === item.id ? 'active' : ''}`}
                  href={lastPaths.current[item.id]}
                  onClick={followLink}
                  aria-current={route === item.id ? 'page' : undefined}
                  aria-label={item.label}
                  title={item.label}
                >
                  <item.icon size={19} strokeWidth={1.7} />
                  <span>{item.label}</span>
                </a>
                {item.id === 'knowledge' && (
                  <nav className="knowledge-subnav" aria-label="知识库分类">
                    {knowledgeLinks.map((child) => (
                      <a
                        key={child.id}
                        className={`nav-link nav-child ${route === 'knowledge' && knowledgeSection === child.id ? 'active' : ''}`}
                        href={knowledgePath(child.id)}
                        onClick={followLink}
                        title={child.label}
                        aria-label={child.label}
                        aria-current={
                          route === 'knowledge' && knowledgeSection === child.id
                            ? 'page'
                            : undefined
                        }
                      >
                        <child.icon size={18} />
                        <span>{child.label}</span>
                      </a>
                    ))}
                  </nav>
                )}
              </div>
            ))}
          </nav>
          <div className="sidebar-footer">
            <Button
              theme="borderless"
              type="tertiary"
              icon={collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
              onClick={() => setCollapsed((value) => !value)}
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              aria-expanded={!collapsed}
              aria-controls="app-navigation"
            >
              <span>{collapsed ? '展开侧边栏' : '收起侧边栏'}</span>
            </Button>
          </div>
        </Layout.Sider>
        <Layout.Content className="workspace-content" {...{ role: 'presentation' }}>
          <main
            className={`shell-main${route === 'chat' ? ' is-chat' : ''}`}
            id="main-content"
            tabIndex={-1}
          >
            <div hidden={route !== 'memory'}>
              <MemoryApp
                workspace={
                  new URLSearchParams(lastPaths.current.chat.split('?')[1]).get('workspace') ?? ''
                }
                active={route === 'memory'}
                hash={route === 'memory' ? hash : lastPaths.current.memory}
              />
            </div>
            <div hidden={route !== 'knowledge'}>
              <KnowledgeApp
                active={route === 'knowledge'}
                hash={route === 'knowledge' ? hash : lastPaths.current.knowledge}
              />
            </div>
            {chatOpened && (
              <div hidden={route !== 'chat'}>
                <Suspense fallback={<p role="status">正在加载对话…</p>}>
                  <ChatApp
                    active={route === 'chat'}
                    hash={route === 'chat' ? hash : lastPaths.current.chat}
                  />
                </Suspense>
              </div>
            )}
          </main>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
