import type { KnowledgeBase, WikiPage } from './types';
import type { GraphData } from './evidence';

// Local, read-only content for visual acceptance; never sent to OpenKB.
const sourceSpecs = [
  ['architecture', 'Icarus 系统设计与分层约束.pdf', 'PDF', 18],
  ['runtime', '插件运行时：事件路由与生命周期.md', 'Markdown', null],
  ['retrieval', '知识检索实验与来源追溯记录.md', 'Markdown', null],
  ['interview', '工作台阅读体验访谈.txt', 'TXT', null],
] as const;
const pageSpecs: [string, string, string, string[]][] = [
  [
    'summaries/architecture',
    'Icarus 系统设计概览',
    '从模型调用到插件协作，梳理各层的职责、依赖方向与一次任务的执行过程。',
    ['sources/architecture.json'],
  ],
  [
    'summaries/retrieval',
    '知识检索与来源追溯实验',
    '比较标题检索、显式链接和来源声明，记录从结论返回原始资料的阅读路径。',
    ['sources/retrieval.json'],
  ],
  [
    'concepts/agent',
    '无状态 Agent',
    '一次执行只使用传入的上下文。历史、记忆和工具装配由外层负责，避免隐含状态影响结果。',
    ['summaries/architecture'],
  ],
  [
    'concepts/events',
    '事件驱动的插件协作',
    '业务通信通过事件传递；运行时按插件身份路由，具体业务含义留在独立插件中。',
    ['sources/architecture.json', 'sources/runtime.json'],
  ],
  [
    'concepts/provenance',
    '来源声明与证据边界',
    '来源声明可以帮助追溯依据，但共同来源不代表两个结论互相支持。',
    ['summaries/retrieval', 'sources/retrieval.json'],
  ],
  [
    'concepts/reading',
    '长文阅读中的上下文保持：目录、来源与返回路径',
    '在较长的标题、摘要和多份来源同时出现时，仍应清晰区分正文、辅助信息与下一步操作。阅读结束后回到原位置，继续浏览。',
    ['sources/interview.json', 'sources/retrieval.json'],
  ],
  [
    'entities/runtime',
    'Plugin Runtime',
    '管理插件注册、生命周期与消息路由的通用基础设施。',
    ['sources/runtime.json'],
  ],
  [
    'concepts/questions',
    '待整理的研究问题',
    '独立笔记：记录尚未补充来源的想法，供后续核对和整理。',
    [],
  ],
];
const links: [string, string][] = [
  ['summaries/architecture', 'concepts/agent'],
  ['summaries/architecture', 'concepts/events'],
  ['concepts/agent', 'entities/runtime'],
  ['concepts/events', 'entities/runtime'],
  ['summaries/retrieval', 'concepts/provenance'],
  ['concepts/reading', 'concepts/provenance'],
];
const pages: WikiPage[] = pageSpecs.map(([path, title, summary, sources]) => ({
  path,
  title,
  summary,
  sources,
  kind: path.split('/')[0] as WikiPage['kind'],
  readState: 'ready',
  content: `# ${title}

${summary}

## 核心观点

知识工作台需要同时回答三个问题：这段内容是什么、它从哪里来、接下来可以阅读什么。正文承载完整论述，来源保留可追溯的入口，关联视图帮助读者探索上下文。

> 这是一份用于界面验收的示例内容，展示阅读和关联布局。

## 阅读与验证

1. 从列表的标题和摘要判断内容是否相关。
2. 打开正文，通过目录定位到需要的章节。
3. 查看声明来源，并返回原来的阅读位置。

| 信息 | 作用 | 核对方式 |
| --- | --- | --- |
| 页面摘要 | 概括内容 | 对照正文 |
| 显式链接 | 连接相关知识 | 打开目标页面 |
| 来源声明 | 追溯原始资料 | 阅读来源 |

### 示例记录

~~~json
{
  "topic": "knowledge-reading",
  "traceable": true,
  "review": "pending"
}
~~~

## 边界与讨论

一个页面可以引用多份资料，同一份资料也可以支撑多个页面。查看关联时应保留关系的解释，避免把共同来源误解为确定的语义关系。较长的描述需要自然换行，同时保持操作入口的位置清楚。

阅读过程中，页面目录用于定位当前文章，知识页面索引用于切换文章。两种导航各自保留清晰的名称和层级，让读者在宽屏与手机上都能理解当前所在位置。

## 延伸阅读

${
  links
    .filter(([from]) => from === path)
    .map(([, to]) => `- [[${to}|${pageSpecs.find(([id]) => id === to)![1]}]]`)
    .join('\n') || '当前没有显式链接，可从声明来源继续阅读。'
}
`,
}));

export const previewBase: KnowledgeBase = {
  name: 'Icarus 示例知识库',
  description: '用于布局验收的只读示例',
  pages,
  documents: sourceSpecs.map(([id, name, display_type, count]) => ({
    hash: 'preview-' + id,
    name,
    display_type,
    pages: count,
    docName: id,
    sourcePath: `wiki/sources/${id}.json`,
    readState: 'ready',
    content: `# ${name}\n\n这是一份用于验收资料阅读布局的示例原文。\n\n## 背景\n\n团队需要从分散的资料中整理出可以检索、阅读和追溯的知识。资料列表保留文件名、格式和关联知识数量，正文展示原始论述。\n\n## 观察记录\n\n- 阅读内容时，希望随时查看相关知识。\n- 返回列表时，保留筛选和滚动位置。\n- 在手机上，正文应优先获得阅读空间。\n\n## 结论\n\n保留原始资料有助于核对摘要、澄清概念和发现遗漏。示例中的格式标签用于展示列表样式，正文为预置 Markdown。`,
  })),
};
export const previewGraph: GraphData = {
  nodes: pages.map((page) => ({
    id: page.path,
    label: page.title,
    type: page.kind,
    description: page.summary,
    sources: page.sources,
    in: links.filter(([, to]) => to === page.path).length,
    out: links.filter(([from]) => from === page.path).length,
  })),
  edges: links.map(([source, target]) => ({ source, target })),
  types: ['summaries', 'concepts', 'entities'],
};
