export type ReadState = 'unloaded' | 'loading' | 'ready' | 'failed';
export type Source = {
  hash: string;
  name: string;
  display_type: string;
  pages: number | null;
  content: string;
  readState: ReadState;
  error?: string;
  docName?: string;
  sourcePath?: string;
};
export type WikiPage = {
  path: string;
  title: string;
  kind: 'concepts' | 'entities' | 'summaries';
  summary: string;
  content: string;
  sources: string[];
  readState: ReadState;
  error?: string;
};
export type KnowledgeBase = {
  name: string;
  description: string;
  documents: Source[];
  pages: WikiPage[];
};
export const kindLabel = { concepts: '概念', entities: '实体', summaries: '摘要' };
export const readStateLabel = (item: Source | WikiPage) =>
  item.readState === 'loading'
    ? '正在读取'
    : item.readState === 'failed'
      ? '读取失败'
      : item.readState === 'unloaded'
        ? '正文未加载'
        : item.content.trim()
          ? '可阅读'
          : '正文不可用';
