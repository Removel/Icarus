import { useState } from 'react';
import { ArrowUpRight, Link2, BookOpen } from 'lucide-react';
import { EmptyState } from '@icarus/ui';
import type { WikiPage } from './demo';

type Relation = { from: WikiPage; to: WikiPage; sources: string[] };

export function getRelations(pages: WikiPage[], sourceNames: string[]) {
  const existing = new Set(sourceNames);
  const relations: Relation[] = [];
  pages.forEach((from, index) => pages.slice(index + 1).forEach(to => {
    const sources = [...new Set(from.sources.filter(name => existing.has(name) && to.sources.includes(name)))];
    if (sources.length) relations.push({ from, to, sources });
  }));
  return relations;
}

export default function Graph({ pages, sourceNames, onSelect }: { pages: WikiPage[]; sourceNames: string[]; onSelect: (page: WikiPage) => void }) {
  const [focused, setFocused] = useState<string | null>(null);
  const relations = getRelations(pages, sourceNames);
  const linked = new Set(relations.flatMap(edge => [edge.from.path, edge.to.path]));
  const isolated = pages.filter(page => !linked.has(page.path));
  const rows = Math.max(1, Math.ceil(pages.length / 3));
  const height = rows * 170;
  const position = (page: WikiPage) => {
    const index = pages.findIndex(item => item.path === page.path);
    return { x: (index % 3 + .5) * 1000 / 3, y: (Math.floor(index / 3) + .5) * 170 };
  };

  if (!pages.length) return <EmptyState title="还没有可关联的知识页面" />;
  return <section className="relations-workspace" aria-label="共同来源关系">
    <header className="relations-heading"><div><Link2 size={16} /><h2>共同来源</h2><span>{relations.length} 组关联</span></div><span>连线表示页面引用了同一份资料</span></header>
    <div className="relation-map" style={{ height }}>
      <svg viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none" aria-hidden="true">
        {relations.map(edge => {
          const from = position(edge.from), to = position(edge.to);
          return <line key={`${edge.from.path}-${edge.to.path}`} className={focused === edge.from.path || focused === edge.to.path ? 'highlighted' : ''} x1={from.x} y1={from.y} x2={to.x} y2={to.y} vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      {pages.map(page => {
        const point = position(page);
        const count = relations.filter(edge => edge.from.path === page.path || edge.to.path === page.path).length;
        return <button key={page.path} className={`relation-node ${focused === page.path ? 'focused' : ''}`} style={{ left: `${point.x / 10}%`, top: `${point.y / height * 100}%` }}
          onMouseEnter={() => setFocused(page.path)} onMouseLeave={() => setFocused(null)} onFocus={() => setFocused(page.path)} onBlur={() => setFocused(null)} onClick={() => onSelect(page)}
          title={`${page.title} · ${count ? `${count} 个关联页面` : '暂无共同来源'}`}><BookOpen size={15} /><span>{page.title}<small>{count ? `${count} 个关联页面` : '独立页面'}</small></span></button>;
      })}
    </div>
    <div className="relation-list" aria-label="关系列表">{relations.length ? relations.map(edge => <div className="relation-item" key={`${edge.from.path}-${edge.to.path}`}>
      <div className="relation-pair"><button onClick={() => onSelect(edge.from)}>{edge.from.title}<ArrowUpRight size={12} /></button><Link2 size={13} /><button onClick={() => onSelect(edge.to)}>{edge.to.title}<ArrowUpRight size={12} /></button></div>
      <span>{edge.sources.join('、')}</span>
    </div>) : <p className="field-hint">这些页面暂时没有共同来源。</p>}</div>
    {isolated.length > 0 && <div className="isolated-pages"><span>独立页面</span>{isolated.map(page => <button key={page.path} onClick={() => onSelect(page)}>{page.title}<ArrowUpRight size={12} /></button>)}</div>}
  </section>;
}
