import { kindLabel, readStateLabel, type KnowledgeBase } from './types';
import type { GraphData, Target } from './evidence';

export default function GraphDetails({
  base,
  graph,
  target,
}: {
  base: KnowledgeBase;
  graph: GraphData;
  target: Target;
}) {
  const page =
    target.kind === 'page' ? base.pages.find((item) => item.path === target.id) : undefined;
  const source =
    target.kind === 'source' ? base.documents.find((item) => item.hash === target.id) : undefined;
  const node =
    target.kind === 'page' ? graph.nodes.find((item) => item.id === target.id) : undefined;
  const category = page ? kindLabel[page.kind] : source ? '原始资料' : '知识页面';
  const description = node?.description || page?.summary;
  const item = page ?? source;
  return (
    <div className="graph-metadata">
      {description && <p>{description}</p>}
      <dl>
        <dt>类型</dt>
        <dd>
          {category}
          {node?.type && node.type !== category ? ' · ' + node.type : ''}
        </dd>
        {node && (
          <>
            <dt>引用 / 被引用</dt>
            <dd>
              {node.out} / {node.in}
            </dd>
          </>
        )}
        {source && (
          <>
            <dt>格式 / 页数</dt>
            <dd>
              {source.display_type} · {source.pages === null ? '页数未提供' : source.pages + ' 页'}
            </dd>
          </>
        )}
        {node && (
          <>
            <dt>声明来源</dt>
            <dd>{node.sources.length} 个</dd>
          </>
        )}
      </dl>
      <details>
        <summary>更多字段</summary>
        <dl>
          <dt>{source ? '资料标识' : '页面路径'}</dt>
          <dd>{target.id}</dd>
          {node && (
            <>
              <dt>索引名称</dt>
              <dd>{node.label}</dd>
            </>
          )}
          {source?.docName && (
            <>
              <dt>文档名称</dt>
              <dd>{source.docName}</dd>
            </>
          )}
          {source?.sourcePath && (
            <>
              <dt>来源路径</dt>
              <dd>{source.sourcePath}</dd>
            </>
          )}
          {item && (
            <>
              <dt>正文状态</dt>
              <dd>{readStateLabel(item)}</dd>
            </>
          )}
          {item?.error && (
            <>
              <dt>读取信息</dt>
              <dd>{item.error}</dd>
            </>
          )}
          {node && node.sources.length > 0 && (
            <>
              <dt>来源声明</dt>
              <dd>
                {node.sources.map((value, index) => (
                  <div key={index}>{value}</div>
                ))}
              </dd>
            </>
          )}
        </dl>
      </details>
    </div>
  );
}
