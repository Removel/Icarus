import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

const parser = unified().use(remarkParse).use(remarkGfm).use(remarkMath);
export type MarkdownBlock = { offset: number; text: string };
export type MarkdownState = { text: string; blocks: MarkdownBlock[]; offset: number };

export function advanceMarkdown(text: string, previous?: MarkdownState): MarkdownState {
  // Reference definitions, footnotes and shortcut links can change earlier blocks.
  // Keep their document-wide context, including definitions arriving much later.
  if (text.includes('[')) return { text, blocks: [], offset: 0 };
  const reusable = previous && text.startsWith(previous.text);
  const blocks = reusable ? [...previous.blocks] : [];
  const offset = reusable ? previous.offset : 0;
  const tail = text.slice(offset);
  const tree = parser.parse(tail);
  const last = tree.children.at(-1);
  const boundary = last?.position?.start.offset ?? 0;
  if (boundary > 0) {
    for (let index = 0; index < tree.children.length - 1; index++) {
      const start = tree.children[index].position!.start.offset!;
      const end = tree.children[index + 1].position!.start.offset!;
      blocks.push({ offset: offset + start, text: tail.slice(start, end) });
    }
  }
  return { text, blocks, offset: offset + boundary };
}

export function provisionalMarkdown(text: string): boolean {
  // Do not interpret a table before its separator or an unfinished fence header.
  if (/^\s*\|[^\n]*\n?$/.test(text)) return true;
  if (/^\s{0,3}(?:`{3,}|~{3,})[^\n]*$/.test(text)) return true;
  // remark-math accepts an unterminated display formula; defer its expensive
  // renderer until the closing delimiter arrives instead of showing an error.
  if (/^\s{0,3}\${2,}/.test(text)) {
    const lines = text.trimEnd().split('\n');
    const opening = lines[0].trim().match(/^\${2,}/)![0].length;
    const closing = lines.at(-1)!.trim();
    return lines.length < 2 || !/^\${2,}$/.test(closing) || closing.length < opening;
  }
  return false;
}
