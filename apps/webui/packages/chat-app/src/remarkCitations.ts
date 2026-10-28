type Node = { type: string; value?: string; url?: string; children?: Node[] };

/** Only transform plain prose; leave code, math, existing links and unknown indices intact. */
export default function remarkCitations({ indices }: { indices: number[] }) {
  const valid = new Set(indices);
  return (tree: Node) => {
    function visit(node: Node) {
      if (
        !node.children ||
        ['link', 'linkReference', 'code', 'inlineCode', 'math', 'inlineMath'].includes(node.type)
      )
        return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== 'text' || !child.value) {
          visit(child);
          return [child];
        }
        const nodes: Node[] = [];
        let cursor = 0;
        for (const match of child.value.matchAll(/\[(\d+)\]/g)) {
          const index = Number(match[1]);
          if (!valid.has(index)) continue;
          if (match.index! > cursor)
            nodes.push({ type: 'text', value: child.value.slice(cursor, match.index) });
          nodes.push({
            type: 'link',
            url: `#web-source-${index}`,
            children: [{ type: 'text', value: `[${index}]` }],
          });
          cursor = match.index! + match[0].length;
        }
        if (!nodes.length) return [child];
        if (cursor < child.value.length)
          nodes.push({ type: 'text', value: child.value.slice(cursor) });
        return nodes;
      });
    }
    visit(tree);
  };
}
