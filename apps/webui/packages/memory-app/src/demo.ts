// Demo view model. Service mapping is documented in docs/api-map.md.
export type MemoryEntry = {
  id: string;
  memory: string;
  user_id: string;
  agent_id: string;
  run_id: string;
  expiration_date: string | null;
  updated_at: string;
  created_at: string;
  metadata: { category: string };
  history: { date: string; label: string; content?: string }[];
};

const contents = [
  ['更喜欢简洁、克制的界面，信息层级清晰，操作尽量直接。', '设计偏好', 'workspace:icarus', 'lin', null],
  ['项目采用 Monorepo 结构，各应用保持独立，共享能力按需提取。', '项目约定', 'workspace:icarus', 'lin', null],
  ['默认使用中文交流，技术名词可以保留英文。', '沟通习惯', 'global', 'lin', null],
  ['前端组件优先使用 Semi Design，保持一致的交互和视觉规范。', '技术选择', 'workspace:icarus', 'lin', null],
  ['回答复杂问题时，先给出结论，再补充必要的解释。', '沟通习惯', 'global', 'chen', null],
  ['每周五整理本周的设计反馈，汇总到项目知识库。', '工作习惯', 'workspace:icarus', 'chen', null],
  ['每天晚上九点整理当天的项目笔记。', '工作习惯', 'workspace:icarus', 'lin', '1970-01-01'],
  ['本轮原型评审安排在九月第一周。', '项目安排', 'workspace:icarus', 'lin', '2026-09-07'],
] as const;

export const initialMemories: MemoryEntry[] = contents.map(([memory, category, run_id, user_id, expiration_date], i) => ({
  id: `mem_${['a8f2c1', 'b6d4e9', 'c3a7f5', 'd9e2b8', 'e5f1a3', 'f2b8d6', 'g4c9e1', 'h7a3f2'][i]}`,
  memory, metadata: { category }, run_id, user_id, expiration_date, agent_id: 'icarus',
  updated_at: `2026-09-${28 - Math.floor(i / 2)}T${String(10 - (i % 2)).padStart(2, '0')}:42:00+08:00`,
  created_at: '2026-09-24T09:12:00+08:00',
  history: i === 0 ? [
    { date: '09.28 · 10:42', label: '更新了记忆', content: '补充了对信息层级和操作方式的偏好。' },
    { date: '09.24 · 09:12', label: '创建记忆', content: '更喜欢简洁、克制的界面。' },
  ] : [{ date: '09.24 · 09:12', label: '创建记忆', content: memory }],
}));

export function isExpired(memory: MemoryEntry) {
  return memory.expiration_date !== null && memory.expiration_date <= new Date().toISOString().slice(0, 10);
}

export function memoryDate(iso: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso));
}
