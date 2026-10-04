import { useState } from 'react';
import { ArrowRight, BookOpen, FileText } from 'lucide-react';
import { Modal, EmptyState } from '@icarus/ui';
import EvidenceCanvas from './EvidenceCanvas';
import GraphDetails from './GraphDetails';
import { type KnowledgeBase, type Source, type WikiPage } from './types';
import {
  relationsFor,
  type GraphData,
  type Relation,
  type RelationKind,
  type Target,
} from './evidence';

const labels: Record<Exclude<RelationKind, 'shared'>, string> = {
  source: '内容来源',
  outlink: '引用的页面',
  backlink: '引用此内容的页面',
};
const order = ['source', 'outlink', 'backlink'] as const;
const relationKey = (relation: Relation) =>
  relation.kind + ':' + relation.target.kind + ':' + relation.target.id;
export default function EvidenceMap({
  inspect,
  onInspectClose,
  dialogsVisible,
  base,
  focus,
  graphData: graph,
  onFocus,
  onReadPage,
  onReadSource,
}: {
  inspect: boolean;
  onInspectClose: () => void;
  dialogsVisible: boolean;
  base: KnowledgeBase;
  focus?: Target;
  graphData: GraphData;
  onFocus: (target: Target, trail: Target[]) => void;
  onReadPage: (page: WikiPage) => void;
  onReadSource: (source: Source) => void;
}) {
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const page =
    focus?.kind === 'page' ? base.pages.find((item) => item.path === focus.id) : undefined;
  const source =
    focus?.kind === 'source' ? base.documents.find((item) => item.hash === focus.id) : undefined;
  const relations = focus ? relationsFor(focus, graph, base.documents) : [];
  function name(target: Target) {
    return target.kind === 'page'
      ? (base.pages.find((item) => item.path === target.id)?.title ??
          graph.nodes.find((item) => item.id === target.id)?.label ??
          target.id)
      : (base.documents.find((item) => item.hash === target.id)?.name ?? target.id);
  }
  function read(target: Target) {
    const item =
      target.kind === 'page'
        ? base.pages.find((item) => item.path === target.id)
        : base.documents.find((item) => item.hash === target.id);
    if (!item) return;
    setInspectorOpen(false);
    if ('path' in item) onReadPage(item);
    else onReadSource(item);
  }
  const groups = order
    .map((kind) => ({ kind, items: relations.filter((item) => item.kind === kind) }))
    .filter((group) => group.items.length);
  const current = page ?? source;
  if (!base.pages.length && !base.documents.length) return <EmptyState title="知识库为空" />;
  return (
    <section className="evidence-workspace" aria-label="文档关联">
      <div className="evidence-layout is-overview">
        <EvidenceCanvas
          base={base}
          graph={graph}
          focus={focus}
          onFocus={read}
          onRelation={(origin, kind, target) => {
            setInspectorOpen(true);
            onFocus(origin, [origin]);
            setSelectedKey(relationKey({ kind, target, evidence: [] }));
          }}
        />
        <Modal
          title="文档关联"
          visible={dialogsVisible && (inspect || inspectorOpen)}
          footer={null}
          width="min(720px, calc(100vw - 32px))"
          onCancel={() => {
            setInspectorOpen(false);
            onInspectClose();
          }}
        >
          <div className="relation-detail">
            {focus && current ? (
              <>
                <h2 className="relation-title">{name(focus)}</h2>
                <GraphDetails base={base} graph={graph} target={focus} />
                {groups.length ? (
                  groups.map((group) => (
                    <section
                      className="relation-section"
                      key={group.kind}
                      aria-label={labels[group.kind]}
                    >
                      <h3>
                        {labels[group.kind]}
                        <span>{group.items.length}</span>
                      </h3>
                      {group.items.map((item) => {
                        const unavailable =
                          item.status === 'missing' || item.status === 'ambiguous';
                        const note =
                          item.status === 'missing'
                            ? '未找到该来源'
                            : item.status === 'ambiguous'
                              ? '有多份同名资料，暂时无法确定来源'
                              : '';
                        return (
                          <button
                            key={relationKey(item)}
                            className={`relation-row${selectedKey === relationKey(item) ? ' is-highlighted' : ''}`}
                            disabled={unavailable}
                            onClick={() => read(item.target)}
                          >
                            {item.target.kind === 'source' ? (
                              <FileText size={16} />
                            ) : (
                              <BookOpen size={16} />
                            )}
                            <span>
                              {name(item.target)}
                              {note && <small>{note}</small>}
                            </span>
                            {!unavailable && <ArrowRight size={15} />}
                          </button>
                        );
                      })}
                    </section>
                  ))
                ) : (
                  <EmptyState title="暂无引用关系" />
                )}
              </>
            ) : (
              <EmptyState title="内容不存在或已被移除" />
            )}
          </div>
        </Modal>
      </div>
    </section>
  );
}
