export type Source = {
  hash: string; name: string; display_type: string; pages: number | null;
  phase: 'ready' | 'failed' | 'compiling';
  content: string;
};
export type WikiPage = { path: string; title: string; kind: 'concepts' | 'entities' | 'summaries'; content: string; sources: string[] };
export type KnowledgeBase = { name: string; description: string; documents: Source[]; pages: WikiPage[] };

export const demoPages: WikiPage[] = [
  { path: 'concepts/agent-architecture', title: 'Agent 分层架构', kind: 'concepts', sources: ['Icarus 架构说明.md', '插件开发指南.pdf'], content: '## 设计原则\nIcarus 采用分层、解耦的架构组织 Agent 系统。每一层有明确的职责和边界，业务能力通过独立插件组合。\n## 核心执行\nReActAgent 保持无状态，负责推理与工具执行。它不依赖 Plugin Runtime、Blackboard 或具体的领域插件。\n## 事件与插件\nEvent 是业务通信机制。Plugin Runtime 只按来源插件身份路由，不解释具体事件类型。Hook 只用于持久化、观测和监督。\n## 与上下文的关系\n记忆和知识分别提供跨会话的偏好信息与可追溯的资料。动态插件上下文进入当前 User Prompt，稳定 System Prompt 保持不变。' },
  { path: 'concepts/plugin-runtime', title: '插件运行时', kind: 'concepts', sources: ['插件开发指南.pdf'], content: '## 独立的业务能力\n具体插件各自拥有独立目录，通过 Event 协作。Plugin Runtime 是通用基础设施，不引入领域依赖。\n## 组件边界\n插件内部的辅助对象是普通组件，不注册为嵌套子插件。' },
  { path: 'concepts/memory-scope', title: '记忆的作用范围', kind: 'concepts', sources: ['记忆接入笔记.md'], content: '## 全局与工作区\n记忆使用 user_id、agent_id 和 run_id 确定上下文边界。全局记忆适合稳定偏好；工作区记忆适合项目事实。\n## 生命周期\n设置有效期可以让临时事实自然失效。手动停用保留内容和历史，之后可以恢复。' },
  { path: 'concepts/design-system', title: '统一设计语言', kind: 'concepts', sources: ['WebUI 设计讨论.md'], content: '## 视觉与交互\n工作台采用统一的布局和 Semi Universe Design 主题。内容以卡片呈现，详情按需打开。\n## 导航原则\n先呈现用户要完成的任务，再展示服务信息。记忆与知识各自保持清晰的操作语义。' },
  { path: 'entities/icarus', title: 'Icarus', kind: 'entities', sources: ['Icarus 架构说明.md'], content: '## 项目\nIcarus 是一个可扩展的 Agent 系统，以 Monorepo 管理各应用。\n## 应用\n包括 Agent、Gateway、TUI、Mem0、OpenKB，以及当前正在设计的 WebUI。' },
  { path: 'summaries/architecture', title: '架构说明摘要', kind: 'summaries', sources: ['Icarus 架构说明.md'], content: '## 资料摘要\n本文档说明了 Icarus 的应用边界、执行层和插件通信机制。\n## 关键结论\n应用保持独立；模型供应商差异封装在 model_provider；AgentPlugin 发布原始执行流。' },
];

export const initialBases: KnowledgeBase[] = [{ name: 'Icarus 项目知识库', description: '架构、开发约定，以及一路积累的设计思考。', documents: [
  { hash: 'doc_a1c92', name: 'Icarus 架构说明.md', display_type: 'MD', pages: null, phase: 'ready', content: demoPages[0].content },
  { hash: 'doc_b8d13', name: '插件开发指南.pdf', display_type: 'PDF', pages: 18, phase: 'ready', content: '## 插件开发指南\n这份示例资料介绍插件注册、事件发布和上下文注入。\n## 开发流程\n为插件建立独立目录，将功能测试放在对应的测试目录中。\n## 测试\n优先运行最小受影响的测试，然后验证应用测试集。' },
  { hash: 'doc_c5e24', name: 'WebUI 设计讨论.md', display_type: 'MD', pages: null, phase: 'ready', content: demoPages[3].content },
  { hash: 'doc_d7f35', name: '记忆接入笔记.md', display_type: 'MD', pages: null, phase: 'ready', content: demoPages[2].content },
  { hash: 'doc_e2a46', name: 'EventBus 接口约定.pdf', display_type: 'PDF', pages: 8, phase: 'ready', content: '## EventBus 接口约定\nEvent 是业务通信机制。Hook 只用于持久化、观测和监督，不能改变主流程行为。\n## 路由\n通用运行时按来源插件身份路由事件，不解释具体领域类型。' },
  { hash: 'doc_f9b57', name: '早期讨论记录.pdf', display_type: 'PDF', pages: null, phase: 'failed', content: '此演示文件用于展示失败后的重试路径。模拟原因：未提取到可读文本。' },
], pages: demoPages }, { name: '阅读与灵感', description: '给新的想法留一点空间。', documents: [], pages: [] }];

export const kindLabel = { concepts: '概念', entities: '实体', summaries: '摘要' };
