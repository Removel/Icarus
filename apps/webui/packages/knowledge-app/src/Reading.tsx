import { useId } from 'react';
import Markdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { visit, SKIP } from 'unist-util-visit';
import type { Root, PhrasingContent, Heading } from 'mdast';

export function excerpt(content: string) {
  const paragraph =
    content.split('\n').find((line) => line.trim() && !line.trim().startsWith('#')) ?? '';
  return paragraph.length > 120 ? `${paragraph.slice(0, 120)}…` : paragraph;
}

function headingText(node: Heading | PhrasingContent): string {
  return 'value' in node
    ? node.value
    : 'children' in node
      ? node.children.map((child) => headingText(child)).join('')
      : 'alt' in node
        ? (node.alt ?? '')
        : '';
}

function wikiLinks() {
  return (tree: Root) => {
    visit(tree, (node, index, parent) => {
      if (node.type === 'link' || node.type === 'linkReference') return SKIP;
      if (node.type !== 'text' || !parent || index === undefined) return;
      const children: PhrasingContent[] = [];
      let offset = 0;
      for (const match of node.value.matchAll(/\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g)) {
        if (match.index > offset)
          children.push({ type: 'text', value: node.value.slice(offset, match.index) });
        children.push({
          type: 'link',
          url: 'icarus-page:' + encodeURIComponent(match[1]),
          children: [{ type: 'text', value: match[2] ?? match[1] }],
        });
        offset = match.index + match[0].length;
      }
      if (!children.length) return;
      if (offset < node.value.length)
        children.push({ type: 'text', value: node.value.slice(offset) });
      parent.children.splice(index, 1, ...children);
      return index + children.length;
    });
  };
}

export default function Reading({
  content,
  title,
  resolveLink,
  showOutline = false,
}: {
  content: string;
  title?: string;
  resolveLink?: (target: string) => (() => void) | undefined;
  showOutline?: boolean;
}) {
  const prefix = useId();
  const headings: { id: string; label: string; depth: number }[] = [];
  function outline() {
    return (tree: Root) => {
      const first = tree.children[0];
      if (
        first?.type === 'heading' &&
        first.depth === 1 &&
        headingText(first).trim() === title?.trim()
      )
        tree.children.shift();
      visit(tree, 'heading', (node) => {
        const id = `${prefix}-heading-${headings.length}`;
        headings.push({ id, label: headingText(node), depth: node.depth });
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id } };
      });
    };
  }
  function jump(id: string) {
    const target = document.getElementById(id);
    target?.scrollIntoView({ block: 'start' });
    if (target) {
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
    }
  }
  // Markdown is synchronous; derive the outline and anchors from the same parse.
  const rendered = Markdown({
    children: content,
    remarkPlugins: [remarkGfm, wikiLinks, outline],
    skipHtml: true,
    urlTransform: (url) => (url.startsWith('icarus-page:') ? url : defaultUrlTransform(url)),
    components: {
      a: ({ href, children }) => {
        if (!href) return <span>{children}</span>;
        if (href.startsWith('icarus-page:')) {
          let target: string;
          try {
            target = decodeURIComponent(href.slice('icarus-page:'.length));
          } catch {
            return <span>{children}</span>;
          }
          const open = resolveLink?.(target);
          return open ? (
            <button className="reading-link" onClick={open}>
              {children}
            </button>
          ) : (
            <span className="reading-missing" title="链接内容不存在或无法确认">
              {children}
            </span>
          );
        }
        if (href.startsWith('#')) {
          let fragment = href.slice(1);
          try {
            fragment = decodeURIComponent(fragment);
          } catch {
            return <span>{children}</span>;
          }
          const target = headings.find(
            (item) =>
              item.id === fragment || item.label.toLowerCase().replace(/\s+/g, '-') === fragment,
          );
          return target ? (
            <button className="reading-link" onClick={() => jump(target.id)}>
              {children}
            </button>
          ) : (
            <span>{children}</span>
          );
        }
        if (/^(https?:\/\/|mailto:)/i.test(href))
          return (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        const open = resolveLink?.(href);
        return open ? (
          <button className="reading-link" onClick={open}>
            {children}
          </button>
        ) : (
          <span className="reading-missing">{children}</span>
        );
      },
      table: ({ children }) => (
        <div className="reading-table" tabIndex={0} role="region" aria-label="正文表格">
          <table>{children}</table>
        </div>
      ),
    },
  });
  return (
    <>
      {showOutline && headings.length > 1 && (
        <details className="reading-outline">
          <summary>文章目录 · {headings.length}</summary>
          <nav aria-label="文章目录">
            {headings.map((item) => (
              <button
                key={item.id}
                style={{ paddingLeft: Math.min(item.depth - 1, 3) * 12 }}
                onClick={() => jump(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>
        </details>
      )}
      <div className="reading">{rendered}</div>
    </>
  );
}
